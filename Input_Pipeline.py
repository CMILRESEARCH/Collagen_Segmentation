# -*- coding: utf-8 -*-
"""
Created on Wed Jul 21 16:35:06 2021

@author: spborder

Data input pipeline for Deep-DUET

from: https://towardsdatascience.com/creating-and-training-a-u-net-model-with-pytorch-for-2d-3d-semantic-segmentation-dataset-fb1f7f80fe55
"""

import os
import pandas as pd
import numpy as np
from math import floor, ceil

from tqdm import tqdm
import matplotlib.pyplot as plt
from skimage.io import imread, imsave
from glob import glob

from random import sample

import torch
from torch.utils.data import Dataset
from torchvision.transforms import ToTensor
import albumentations

from skimage.transform import resize

from Augmentation_Functions import *
from CollagenSegUtils import resize_special

# Input class with required len and getitem functions
# in this case, the inputs and targets are lists of paths matching paths for image and segmentation ground-truth
# *** Have to make sure the input and target paths are aligned ***
# Need to change shape of input data to be (batch size, channels, height, width)
# with linear scaling depending on network expectations,
# target to be (batch size, height, width) with dense integer encoding for each class

class SegmentationDataSet(Dataset):
    def __init__(self,
                 inputs: list,
                 targets: list,
                 transform = None,
                 pre_transform = None,
                 target_type = None,
                 batch_size = None,
                 parameters = {}):
        

        self.inputs = inputs
        self.targets = targets
        self.transform = transform
        self.inputs_dtype = torch.float32
        self.parameters = parameters

        if target_type == 'binary':
            self.targets_dtype = torch.long
        elif target_type == 'nonbinary':
            self.targets_dtype = torch.float32
        elif target_type is None:
            self.targets_dtype = torch.float32
        
        self.batch_size = batch_size
        self.patch_batch = False
        self.sample_weight = False

        self.testing_metrics = False if len(self.targets)==0 else True

        # increasing dataset loading efficiency
        self.pre_transform = pre_transform
        
        self.cached_data = []
        self.cached_names = []
        self.image_means = []
        self.image_stds = []
        self.images = []
        self.cached_item_names = []

        image_size = [int(i) for i in self.parameters['preprocessing']['image_size'].split(',')]
        
        progressbar = tqdm(range(len(self.inputs)), desc = 'Caching')

        if len(self.targets)>0:
            # For a training round or testing round with ground truth labels available
            for i, img_name, tar_name in zip(progressbar, self.inputs, self.targets):
                try:
                    # For multi-channel image inputs
                    if type(img_name)==list:
                        img1,img2,tar = imread(str(img_name[0])), imread(str(img_name[1])),imread(str(tar_name))
                        img = np.concatenate((img1,img2),axis=-1)
                        img_name = img_name[0]
                    else:
                        img, tar = imread(str(img_name)), imread(str(tar_name))

                    self.images.append((img,tar))
                    self.cached_item_names.append(img_name)
                except FileNotFoundError:
                    print(f'File not found: {img_name}, {tar_name}')
        
        else:
            # For just predicting on images with no ground truth provided
            for i, img_name in zip(progressbar,self.inputs):
                try:
                    if type(img_name)==list:
                        img1,img2 = imread(str(img_name[0])),imread(str(img_name[1]))
                        img = np.concatenate((img1,img2),axis=-1)
                        img_name = img_name[0]
                    else:
                        img = imread(str(img_name))
                    
                    tar = np.zeros((np.shape(img)[0],np.shape(img)[1],1))

                    self.images.append((img,tar))
                    self.cached_item_names.append(img_name)

                except FileNotFoundError:
                    print(f'File not found: {img_name}')
        
        # For images that are smaller/same size as the model's patch size then just resize as normal   
        for (img, tar),name in tqdm(zip(self.images,self.cached_item_names),total=len(self.images),desc = 'Preprocessing Images'):     

            if np.shape(img)[0]<=image_size[0] and np.shape(img)[1]<=image_size[1]:
           
                if self.pre_transform is not None:
                    img, tar = self.pre_transform(img, tar)

                # Calculating dataset mean and standard deviation
                img_channel_mean = np.mean(img,axis=(0,1))
                img_channel_std = np.std(img,axis=(0,1))

                self.image_means.append(img_channel_mean)
                self.image_stds.append(img_channel_std)
                
                # Data used for training and testing
                self.cached_data.append((img,tar))
                self.cached_names.append(name)
            
            else:

                # Overlap percentage, hardcoded patch size
                self.patch_size = [image_size[0],image_size[1]]
                self.patch_batch = 0.75
                stride = [int(self.patch_size[0]*(1-self.patch_batch)), int(self.patch_size[1]*(1-self.patch_batch))]

                # Calculating and storing patch coordinates for each image and reading those regions at training time :/
                n_patches = [1+floor((np.shape(img)[0]-self.patch_size[0])/stride[0]), 1+floor((np.shape(img)[1]-self.patch_size[1])/stride[1])]
                #print(f'{n_patches} Patches')
                start_coords = [0,0]

                row_starts = [int(start_coords[0]+(i*stride[0])) for i in range(0,n_patches[0])]
                col_starts = [int(start_coords[1]+(i*stride[1])) for i in range(0,n_patches[1])]
                row_starts.append(int(np.shape(img)[0]-self.patch_size[0]))
                col_starts.append(int(np.shape(img)[1]-self.patch_size[1]))
                
                self.original_image_size = list(np.shape(img))

                # Iterating through the row_starts and col_starts lists and applying pre_transforms to the image
                item_patches = []
                patch_names = []
                for r_s in row_starts:
                    for c_s in col_starts:
                        new_img = img[r_s:r_s+self.patch_size[0], c_s:c_s+self.patch_size[1],:]
                        new_tar = np.zeros((np.shape(new_img)[0],np.shape(new_img)[1]))

                        if self.pre_transform is not None:
                            new_img, new_tar = self.pre_transform(new_img, new_tar)

                        #img_channel_mean = np.mean(new_img,axis=(0,1))
                        #img_channel_std = np.std(img,axis=(0,1))
                        #self.image_means.append(img_channel_mean)
                        #self.image_stds.append(img_channel_std)
                        #print(f'patch:{len(item_patches)+1} created')
                        item_patches.append((new_img,new_tar))
                        patch_names.append(img_name.replace(f'.{img_name.split(".")[-1]}',f'_{r_s}_{c_s}.{img_name.split(".")[-1]}'))

                self.cached_data.append(item_patches)
                self.cached_names.append(patch_names)
                
                self.cached_item_patches = [len(i) for i in self.cached_data]
                self.cached_item_index = 0

        print(f'Cached Data: {len(self.cached_data)}')
        #print(f'image_means mean: {np.mean(self.image_means,axis=0)}')
        #print(f'image_stds mean: {np.mean(self.image_stds,axis=0)}')


    def __len__(self):
        
        if not self.patch_batch:
            return len(self.cached_data)
        else:
            return sum([len(i) for i in self.cached_data])
        
    
    # Getting matching input and target(label)
    def __getitem__(self,
                    index: int):
        
        if not self.patch_batch:
            x, y = self.cached_data[index]
            input_ID = self.cached_names[index]
        else:
            # Getting the adjusted index to use
            if index>=len(self.cached_data[self.cached_item_index])-1:
                self.cached_item_index+=1
                index -= sum(self.cached_item_patches[0:self.cached_item_index])
            try:
                x, y = self.cached_data[self.cached_item_index][index]
                input_ID = self.cached_names[self.cached_item_index][index]
            except IndexError:
                print(f'index: {index}')
                print(f'len of self.cached_data: {len(self.cached_data)}')
                print(f'self.cached_item_index: {self.cached_item_index}')
                print(f'sum(self.cached_item_patches[0:self.cached_item_index]): {sum(self.cached_item_patches[0:self.cached_item_index])}')
                print(f'self.cached_item_patches: {self.cached_item_patches}')

        # Preprocessing steps (if there are any)
        if self.transform is not None:
            x, y = self.transform(x, y)
        
        # Getting in the right input/target data types
        x, y = torch.from_numpy(x).type(self.inputs_dtype), torch.from_numpy(y).type(self.targets_dtype)
        return x, y, input_ID
    
    def add_sub_categories(self,sub_categories, sub_cat_column):
        # Implementing sub-category weighting if provided with dataframe
        sub_cat_counts = sub_categories[sub_cat_column].value_counts(normalize=True).to_dict()
        print(f'Dataset composition: {sub_cat_counts}')

        # Applying inverse weight to each sample (evens out sampling)
        sub_cat_counts = {i:1.0-j for i,j in zip(list(sub_cat_counts.keys()),list(sub_cat_counts.values()))}

        # Calculating sample weight
        self.sample_weight = []
        for s in self.cached_names:
            sample_name = s.split('/')[-1]
            print(sample_name)
            if sample_name in sub_categories['Image_Names'].tolist():
                sample_label = sub_categories[sub_categories['Image_Names'].str.match(sample_name)]['Labels'].tolist()[0]
                print(sample_label)
                self.sample_weight.append(sub_cat_counts[sample_label])
            else:
                print(f'sample not in dataframe: {sample_name}')
                self.sample_weight.append(0)

        self.sample_weight = [i/sum(self.sample_weight) for i in self.sample_weight]
        print(f'sample_weight: {self.sample_weight}')
    
    def __iter__(self):

        return self
    
    def __next__(self):

        # Used during training when there is a sample_weight
        img_list = []
        tar_list = []
        name_list = []
        for b in range(self.batch_size):
            
            if self.sample_weight:
                s_idx = np.random.choice(list(range(len(self.cached_data))),p=self.sample_weight)

            img, tar = self.cached_data[s_idx]
            input_id = self.cached_names[s_idx]
            
            if self.transform is not None:
                x, y = self.transform(img,tar)
            
            x, y = torch.from_numpy(x).type(self.inputs_dtype), torch.from_numpy(y).type(self.targets_dtype)

            img_list.append(x)
            tar_list.append(y)
            name_list.append(input_id)

        return torch.stack(img_list), torch.stack(tar_list), name_list

    def normalize_cache(self,means,stds):
        # Applying normalization to a dataset according to a given set of means and standard deviations per channel
        for img,tar in self.cached_data:
            img = np.float64(img)
            for j in range(img.shape[-1]):
                img[:,:,j] -= means[j]
                img[:,:,j] /= stds[j]


def make_training_set(phase,train_img_paths, train_tar, valid_img_paths, valid_tar,parameters):
    
    img_size = [int(i) for i in parameters['preprocessing']['image_size'].split(',')]
    mask_size = [int(i) for i in parameters['preprocessing']['mask_size'].split(',')]
    color_transform = parameters['preprocessing']['color_transform']

    image_means = [float(i) for i in parameters['preprocessing']['image_means'].split(',')]
    image_stds = [float(i) for i in parameters['preprocessing']['image_stds'].split(',')]

    if phase == 'train':

        pre_transforms = ComposeDouble([
                FunctionWrapperDouble(resize_special,
                                    input = True,
                                    target = False,
                                    output_size = img_size,
                                    transform = color_transform),
                FunctionWrapperDouble(resize,
                                    input = False,
                                    target = True,
                                    output_shape = mask_size),
                FunctionWrapperDouble(normalize,
                                      input = True,
                                      target = False,
                                      mean = image_means,
                                      std = image_stds)
        ])        

        # Continuous target type augmentations
        transforms_training = ComposeDouble([
            AlbuSeg2d(albumentations.HorizontalFlip(p=0.5)),
            #AlbuSeg2d(albumentations.IAAPerspective(p=0.5)),
            AlbuSeg2d(albumentations.ShiftScaleRotate(shift_limit=0.0625, scale_limit=0.5,rotate_limit=45,interpolation=1,p=0.1)),
            AlbuSeg2d(albumentations.VerticalFlip(p=0.5)),
            FunctionWrapperDouble(np.moveaxis, input = True, target = True, source = -1, destination = 0)
        ])

        transforms_validation = ComposeDouble([
            FunctionWrapperDouble(np.moveaxis,input=True,target=True,source=-1,destination=0)
        ])

        dataset_train = SegmentationDataSet(inputs = train_img_paths,
                                             targets = train_tar,
                                             transform = transforms_training,
                                             pre_transform = pre_transforms,
                                             parameters = parameters)
        
        dataset_valid = SegmentationDataSet(inputs = valid_img_paths,
                                             targets = valid_tar,
                                             transform = transforms_validation,
                                             pre_transform = pre_transforms,
                                             parameters = parameters)
        
    elif phase == 'test':

        pre_transforms = ComposeDouble([
                FunctionWrapperDouble(resize_special,
                                    input = True,
                                    target = False,
                                    output_size = img_size,
                                    transform = color_transform),
                FunctionWrapperDouble(resize,
                                    input = False,
                                    target = True,
                                    output_shape = mask_size),
                FunctionWrapperDouble(normalize,
                                        input = True,
                                        target = False,
                                        mean = image_means,
                                        std = image_stds)
            ])
        
        transforms_testing = ComposeDouble([
                FunctionWrapperDouble(np.moveaxis, input = True, target = True, source = -1, destination = 0),
                ])

        # this is 'None' because we are just testing the network
        dataset_train = None
        
        dataset_valid = SegmentationDataSet(inputs = valid_img_paths,
                                            targets = valid_tar,
                                            transform = transforms_testing,
                                            pre_transform = pre_transforms,
                                            parameters = parameters)


    return dataset_train, dataset_valid
    















