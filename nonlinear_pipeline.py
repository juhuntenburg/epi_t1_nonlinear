# -*- coding: utf-8 -*-

##### import libraries 
from nipype.pipeline.engine import Node, Workflow
import nipype.interfaces.io as nio
import nipype.interfaces.utility as util
import nipype.interfaces.fsl as fsl
import nipype.interfaces.ants as ants
import nipype.interfaces.c3 as c3
import nipype.interfaces.freesurfer as fs
import os
from nipype.utils.filemanip import filename_to_list

###### initiate workflow and set directories 
nonreg = Workflow(name='epi_t1_nonlinear')
nonreg.base_dir='/scr/ilz1/nonlinear_registration/nki/optimisation/b_redo_old/working_dir'
data_dir = '/scr/kalifornien1/data/nki_enhanced/'
output_dir = '/scr/ilz1/nonlinear_registration/nki/b_redo_old'
subjects = ['0109727','0111282','0144667']
#folder = !ls '/scr/kalifornien1/data/nki_enhanced/niftis'
#subjects = folder[0:221]


##### inforsource to iterate over subjects
infosource = Node(util.IdentityInterface(fields=['subject_id']), 
                  name="infosource")
infosource.iterables=('subject_id', subjects)


##### datasource to grab data
datasource = Node(nio.DataGrabber(infields=['subject_id'], 
                              outfields=['func','anat','ribbon'],
                              base_directory = os.path.abspath(data_dir),
                              template = '%s/%s/%s',
                              template_args=dict(func=[['niftis','subject_id','RfMRI_std_2500.nii.gz']], 
                                                 anat=[['freesurfer','subject_id','mri/brain.mgz']],
                                                 ribbon=[['freesurfer','subject_id','mri/ribbon.mgz']]),
                              sort_filelist = True),
                  name='datasource')

nonreg.connect(infosource, 'subject_id', datasource, 'subject_id')


###### sink to store data
sink = Node(nio.DataSink(base_directory=output_dir,
                         substitutions=('_subject_id_', '')), 
            name='sink')

nonreg.connect(infosource, 'subject_id', sink, 'container')


##### realignment to mean volume with mcflirt
mcflirt = Node(fsl.MCFLIRT(output_type = 'NIFTI_GZ',
                                 mean_vol = True,
                                 save_plots = True, 
                                 save_mats = True,
                                 stats_imgs = True), 
               name='mcflirt')

nonreg.connect(datasource, 'func', mcflirt, 'in_file')
nonreg.connect(mcflirt, 'out_file', sink, 'realignment')

nonreg.connect(mcflirt, 'par_file',sink, 'realignment.@parameters')
nonreg.connect(mcflirt, 'mat_file',sink, 'realignment.@matrices')


##### calculate temporal mean
calc_mean = Node(fsl.maths.MeanImage(dimension='T',
                                     output_type = 'NIFTI_GZ'), 
                 name='tmean')

nonreg.connect(mcflirt, 'out_file', calc_mean, 'in_file')
nonreg.connect(calc_mean, 'out_file',sink, 'realignment.@realigned_mean')


##### convert brain.mgz to nifti
mriconvert = Node(fs.MRIConvert(out_type='niigz'), 
                  name='mgz2nifti')

nonreg.connect(datasource, 'anat', mriconvert, 'in_file')
nonreg.connect(mriconvert, 'out_file', sink, 'anat.@brain')


##### calculate rigid transform mean epi to freesurfer t1 with bbregister
bbregister = Node(fs.BBRegister(init='fsl', 
                                       contrast_type='t2', 
                                       out_fsl_file = True, 
                                       registered_file=True,
                                       subjects_dir=os.path.abspath('/scr/kalifornien1/data/nki_enhanced/freesurfer')), 
                  name='bbregister')

nonreg.connect(infosource, 'subject_id', bbregister, 'subject_id')
nonreg.connect(calc_mean, 'out_file', bbregister, 'source_file')
nonreg.connect(bbregister, 'out_fsl_file', sink, 'lin_transform.@fsl_lintransform')


##### convert transformation to itk format
c3daffine = Node(c3.C3dAffineTool(fsl2ras=True,
                                        itk_transform=True), 
                name='fsl2itk')

nonreg.connect(calc_mean, 'out_file', c3daffine, 'source_file')
nonreg.connect(mriconvert, 'out_file', c3daffine, 'reference_file')
nonreg.connect(bbregister, 'out_fsl_file', c3daffine, 'transform_file')
nonreg.connect(c3daffine, 'itk_transform', sink, 'lin_transform.@itk_lintransform')


##### binarize and dilate ribbon
bin_dil = Node(fs.model.Binarize(dilate=3,
                                   min=0.1,
                                   out_type='nii.gz'), 
                name='ribbon_mask')

nonreg.connect(datasource, 'ribbon', bin_dil, 'in_file')


##### create bounding box mask and rigidly transform into freesurfer anatomical space
boundingbox = Node(fs.model.Binarize(min=0.0,
                                      out_type='nii.gz'), 
                name='boundingbox_mask')

nonreg.connect(calc_mean, 'out_file', boundingbox, 'in_file')

transformbb = Node(ants.resampling.ApplyTransforms(dimension=3,
                                             interpolation='NearestNeighbor'),
                   name='boundingbox_mask2fs')

nonreg.connect(c3daffine, ('itk_transform', filename_to_list), transformbb, 'transforms')
nonreg.connect(boundingbox, 'binary_file', transformbb, 'input_image')
nonreg.connect(bin_dil, 'binary_file', transformbb, 'reference_image')


##### intersect both masks
intersect = Node(fsl.maths.BinaryMaths(operation = 'mul'), 
                 name = 'combined_masks')

nonreg.connect(bin_dil, 'binary_file', intersect, 'in_file')
nonreg.connect(transformbb, 'output_image', intersect, 'operand_file')
nonreg.connect(intersect, 'out_file', sink, 'mask.@combined_mask_fsvspace')


##### mask anatomical 
maskanat = Node(fs.utils.ApplyMask(), 
                name='apply_combined_mask_2anat')

nonreg.connect(intersect, 'out_file', maskanat, 'mask_file')
nonreg.connect(mriconvert, 'out_file', maskanat, 'in_file')
nonreg.connect(maskanat, 'out_file', sink, 'anat.@masked_brain')


##### invert masked anatomical
anat_min_max = Node(fsl.utils.ImageStats(op_string = '-R'), name='derive_anat_intensities')
epi_min_max = Node(fsl.utils.ImageStats(op_string = '-R'), name='derive_epi_intensities')

nonreg.connect(maskanat, 'out_file', anat_min_max, 'in_file') 
nonreg.connect(calc_mean, 'out_file', epi_min_max, 'in_file') 


##### function to calculate add and multiply values from image stats
def calculate_inversion(anat_min_max, epi_min_max):
    
    mul_fac = -(epi_min_max[1]-epi_min_max[0])/(anat_min_max[1]-anat_min_max[0])
    add_fac = abs(anat_min_max[1]*mul_fac)+epi_min_max[0]
    
    return mul_fac, add_fac

calcinv = Node(util.Function(input_names=['anat_min_max', 'epi_min_max'],
                                 output_names=['mul_fac', 'add_fac'],
                                 function=calculate_inversion),
              name='inversion')


nonreg.connect(anat_min_max, 'out_stat', calcinv, 'anat_min_max')
nonreg.connect(epi_min_max, 'out_stat', calcinv, 'epi_min_max')


mulinv = Node(fsl.maths.BinaryMaths(operation='mul'), name='multiply_inv')
addinv = Node(fsl.maths.BinaryMaths(operation='add'), name='add_inv')

nonreg.connect(maskanat, 'out_file', mulinv, 'in_file')
nonreg.connect(calcinv, 'mul_fac', mulinv, 'operand_value')
nonreg.connect(mulinv, 'out_file', addinv, 'in_file')
nonreg.connect(calcinv, 'add_fac', addinv, 'operand_value')
nonreg.connect(addinv, 'out_file', sink, 'anat.@inv_masked_brain')


##### inversly transform mask and mask original epi
transformmask = Node(ants.resampling.ApplyTransforms(dimension=3,
                                             interpolation='NearestNeighbor',
                                             invert_transform_flags=[True]), 
                     name = 'combinedmask2epi')

nonreg.connect(c3daffine, ('itk_transform', filename_to_list), transformmask, 'transforms')
nonreg.connect(intersect, 'out_file',  transformmask, 'input_image')
nonreg.connect(calc_mean, 'out_file', transformmask, 'reference_image')
nonreg.connect(transformmask, 'output_image', sink, 'mask.@combined_mask_epispace')


mask_orig_epi = Node(fs.utils.ApplyMask(), 
                name='mask_orig_epi')

nonreg.connect(transformmask, 'output_image', mask_orig_epi, 'mask_file')
nonreg.connect(calc_mean, 'out_file', mask_orig_epi, 'in_file')
nonreg.connect(mask_orig_epi, 'out_file', sink, 'realignment.@masked_epi')


# nonlinear registration with ants
antsregistration = Node(ants.registration.Registration(dimension = 3,
                                                       invert_initial_moving_transform = True,
                                                       metric = ['CC'],
                                                       metric_weight = [1.0],
                                                       radius_or_number_of_bins = [4],
                                                       sampling_percentage = [0.3],
                                                       sampling_strategy = ['Regular'],
                                                       transforms = ['SyN'],
                                                       args = '-g .1x1x.1',
                                                       transform_parameters = [(0.20,3,0)],
                                                       number_of_iterations = [[10,5]],
                                                       convergence_threshold = [1e-06],
                                                       convergence_window_size = [10],
                                                       shrink_factors = [[2,1]],
                                                       smoothing_sigmas = [[1,0.5]],
                                                       sigma_units = ['vox'],
                                                       use_estimate_learning_rate_once = [True],
                                                       use_histogram_matching = [True],
                                                       collapse_output_transforms = True,
                                                       output_inverse_warped_image = True,
                                                       output_warped_image = True),
                        name = 'antsregistration')

nonreg.connect(c3daffine, 'itk_transform', antsregistration, 'initial_moving_transform')
nonreg.connect(mask_orig_epi, 'out_file', antsregistration, 'fixed_image')
nonreg.connect(addinv, 'out_file', antsregistration, 'moving_image')
nonreg.connect(antsregistration, 'inverse_warped_image' , sink, 'nonlin_transform.@masked_nonlin_inv_warp')
nonreg.connect(antsregistration, 'warped_image' , sink, 'nonlin_transform.@masked_nonlin_warp')
nonreg.connect(antsregistration, 'reverse_transforms', sink, 'nonlin_transform.@masked_nonlin_inv_deform_field')
nonreg.connect(antsregistration, 'forward_transforms', sink, 'nonlin_transform.@masked_nonlin_deform_field')


##### apply linear transform to epi (and mask it)
lin_epi = Node(ants.resampling.ApplyTransforms(dimension=3),
                   name='lintrans_epi')

nonreg.connect(c3daffine, ('itk_transform', filename_to_list), lin_epi, 'transforms')
nonreg.connect(calc_mean, 'out_file', lin_epi, 'input_image')
nonreg.connect(addinv, 'out_file', lin_epi, 'reference_image')
nonreg.connect(lin_epi, 'output_image', sink, 'lin_transform.@lin_warp')


mask_lin_epi = Node(fs.utils.ApplyMask(), 
                name='mask_lintrans_epi')

nonreg.connect(intersect, 'out_file', mask_lin_epi, 'mask_file')
nonreg.connect(lin_epi, 'output_image', mask_lin_epi, 'in_file')
nonreg.connect(mask_lin_epi, 'out_file', sink, 'lin_transform.@masked_lin_warp')


##### apply nonlinear transform to unmasked epi
def make_list_total(lin_transform, inv_nonlin_transform):
    total_list = [lin_transform, inv_nonlin_transform[1]]
    return total_list

list_total_transform = Node(util.Function(input_names=['lin_transform', 'inv_nonlin_transform'],
                                 output_names=['total_list'],
                                 function=make_list_total),
              name='transformlist')

nonreg.connect(c3daffine, 'itk_transform', list_total_transform, 'lin_transform')
nonreg.connect(antsregistration, 'reverse_transforms', list_total_transform, 'inv_nonlin_transform')


nonlin_orig = Node(ants.resampling.ApplyTransforms(dimension=3), 
                   name='nonlintrans_epi')

nonreg.connect(list_total_transform, 'total_list', nonlin_orig, 'transforms')
nonreg.connect(calc_mean, 'out_file', nonlin_orig, 'input_image')
nonreg.connect(addinv, 'out_file',  nonlin_orig, 'reference_image')
nonreg.connect(nonlin_orig, 'output_image', sink, 'nonlin_transform.@nonlin_inv_warp')


nonreg.run()

#nonreg.run(plugin='CondorDAGMan')
#nonreg.write_graph(graph2use='flat')


##### apply linear and nonlinear transform to whole timeseries
#from nipype.interfaces.ants.resampling import WarpTimeSeriesImageMultiTransform
#lin_trans_ts = Node(WarpTimeSeriesImageMultiTransform(dimension=4),
#                name = 'linearly_transform_timeseries')

#nonreg.connect(list_transform, 'lin_list', lin_trans_ts, 'transformation_series')
#nonreg.connect(mcflirt, 'out_file', lin_trans_ts, 'input_image')
#nonreg.connect(addinv, 'out_file', lin_trans_ts, 'reference_image')
#nonreg.connect(lin_trans_ts, 'output_image', sink, 'lin_transform.@lin_timeseries')

#lin_trans_ts = Node(interface=ApplyXfm(output_type = 'NIFTI_GZ',
#                                       apply_xfm=True),
#                     name = 'linearly_transform_timeseries')
#
#nonreg.connect(c3daffine, 'itk_transform', lin_trans_ts, 'in_matrix_file')
#nonreg.connect(mcflirt, 'out_file', lin_trans_ts, 'in_file')
#nonreg.connect(addinv, 'out_file', lin_trans_ts, 'reference')
#nonreg.connect(lin_trans_ts, 'out_file', sink, 'lin_transform.@lin_timeseries')

#def rev_list_total(lin_transform, inv_nonlin_transform):
#    rev_list = [inv_nonlin_transform[1], lin_transform]
#    return rev_list

#rev_list_transform = Node(interface=Function(input_names=['lin_transform', 'inv_nonlin_transform'],
#                                 output_names=['rev_list'],
#                                 function=rev_list_total),
#              name='rev_transformation_list')

#nonreg.connect(c3daffine, 'itk_transform', rev_list_transform, 'lin_transform')
#nonreg.connect(ants, 'reverse_transforms', rev_list_transform, 'inv_nonlin_transform')



#nonlin_trans_ts = Node(WarpTimeSeriesImageMultiTransform(dimension=4),
#                name = 'nonlinearly_transform_timeseries')

#nonreg.connect(rev_list_transform, 'rev_list', nonlin_trans_ts, 'transformation_series')
#nonreg.connect(mcflirt, 'out_file', nonlin_trans_ts, 'input_image')
#nonreg.connect(addinv, 'out_file', nonlin_trans_ts, 'reference_image')
#nonreg.connect(nonlin_trans_ts, 'output_image', sink, 'nonlin_transform.@nonlin_timeseries')


#nonlin_trans_ts = Node(interface=ApplyWarp(output_type = 'NIFTI_GZ'),
#                     name = 'nonlinearly_transform_timeseries')
#
#nonreg.connect(bbregister, 'out_fsl_file', nonlin_trans_ts, 'premat')
#nonreg.connect(reduce_fields, 'nonlin_trans', nonlin_trans_ts, 'field_file')
#nonreg.connect(mcflirt, 'out_file', nonlin_trans_ts, 'in_file')
#nonreg.connect(addinv, 'out_file',  nonlin_trans_ts, 'ref_file')
#nonreg.connect(nonlin_trans_ts, 'out_file', sink, 'nonlin_transform.@nonlin_timeseries')



##### resample time series
#from nipype.interfaces.afni import Resample
#resamp_lin = Node(interface=Resample(outputtype='NIFTI_GZ',
#                                 voxel_size = (3,3,3)),
#              name = 'resample_lin_ts')

#nonreg.connect(lin_trans_ts, 'output_image', resamp_lin, 'in_file')
#nonreg.connect(resamp_lin,'out_file', sink, 'lin_transform.@lin_timeseries')

#resamp_nonlin = Node(interface=Resample(outputtype='NIFTI_GZ',
#                                 voxel_size = (3,3,3)),
#              name = 'resample_nonlin_ts')

#nonreg.connect(nonlin_trans_ts, 'output_image', resamp_nonlin, 'in_file')
#nonreg.connect(resamp_nonlin,'out_file', sink, 'nonlin_transform.@lin_timeseries')


##### resample anatomical for reference (problem: edges)
#from nipype.interfaces.afni import Resample
#resamp = Node(interface=Resample(outputtype='NIFTI_GZ',
##                                 voxel_size = (3,3,3)),
#              name = 'resample_ref')
#
#nonreg.connect(addinv, 'out_file', resamp, 'in_file')
#nonreg.connect(resamp,'out_file', sink, 'anat.@resamp_brain')
