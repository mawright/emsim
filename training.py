"""
training.py

Methods for training (and validation) of EM electron model.
"""
import h5py
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torchvision

from torch.utils.data import Dataset
from torch.autograd import Variable
from PIL import Image

import emnet

# Flag for data augmentation
augment = False

# Modified from medicaltorch.transforms: https://github.com/perone/medicaltorch/blob/master/medicaltorch/transforms.py
def rotate3D(data,axis=0):
    angle = np.random.uniform(-20,20)
    drot  = np.zeros(data.shape, dtype=data.dtype)

    for x in range(data.shape[axis]):
        if axis == 0:
            drot[x,:,:] = Image.fromarray(data[x,:,:]).rotate(angle,resample=False,expand=False,center=None,fillcolor=0)
        if axis == 1:
            drot[:,x,:] = Image.fromarray(data[:,x,:]).rotate(angle,resample=False,expand=False,center=None,fillcolor=0)
        if axis == 2:
            drot[:,:,x] = Image.fromarray(data[:,:,x]).rotate(angle,resample=False,expand=False,center=None,fillcolor=0)

    return drot

# Add a random gaussian noise to the data.
def gaussnoise(data, mean=0.0, stdev=0.05):
    dnoise = data + np.random.normal(loc=mean,scale=stdev,size=data.shape)
    return dnoise


class EMDataset(Dataset):
    def __init__(self, dframe, noise_mean=0, noise_sigma=0, nstart=0, nend=0, add_noise=False, add_shift=0, augment=False):

        # Save some inputs for later use.
        self.dframe = dframe
        self.augment = augment
        self.noise_mean = noise_mean
        self.noise_sigma = noise_sigma
        self.add_noise = add_noise
        self.add_shift = add_shift

        # Open the dataframe.
        self.df_data = pd.read_pickle(dframe)

        # Extract the events array.
        self.events = self.df_data.event.unique()

        # Select the specified range [nstart:nend] for this dataset.
        if(nend == 0):
            self.events = self.events[nstart:]
            print("Created dataset for events from",nstart,"to",len(self.events))
        else:
            self.events = self.events[nstart:nend]
            print("Created dataset for events from",nstart,"to",nend)

    def __len__(self):
        return len(self.events)

    def __getitem__(self, idx):

        # Get the event ID corresponding to this key.
        evt = self.events[idx]

        # Prepare the event.
        evt_arr = np.zeros([101,101])
        df_evt = self.df_data[self.df_data.event == evt]
        for row,col,counts in zip(df_evt['row'].values,df_evt['col'].values,df_evt['counts'].values):
            evt_arr[row,col] += counts

        # Use an 11x11 event+noise to determine the maximum pixel.
        evt_small = evt_arr[50-int((11-1)/2):50+int((11-1)/2)+1,50-int((11-1)/2):50+int((11-1)/2)+1]
        if(self.add_noise):
            evt_small = gaussnoise(evt_small, mean=self.noise_mean, stdev=self.noise_sigma)
        yx_shift = np.unravel_index(np.argmax(evt_small),evt_small.shape)
        y_shift = yx_shift[0] - 5
        x_shift = yx_shift[1] - 5
        #print("Argmax was {}".format(yx_shift))
        #print("Found x-shift = {}, y-shift = {}".format(x_shift,y_shift))

        # Extract the specified event size from the larger event, centered on the maximum pixel.
        evt_arr = evt_arr[50+y_shift-int((emnet.EVT_SIZE-1)/2):50+y_shift+int((emnet.EVT_SIZE-1)/2)+1,50+x_shift-int((emnet.EVT_SIZE-1)/2):50+x_shift+int((emnet.EVT_SIZE-1)/2)+1]

        # Normalize to value of greatest magnitude = 1.
        #evt_arr /= 10000 #np.max(np.abs(evt_arr))

        # Add a manual shift, if specified.
        # x_shift = 0.
        # y_shift = 0.
        # if(self.add_shift > 0):
        #
        #     x_shift = np.random.randint(-self.add_shift,self.add_shift)
        #     y_shift = np.random.randint(-self.add_shift,self.add_shift)
        #
        #     evt_arr = np.roll(evt_arr,x_shift,axis=1)
        #     evt_arr = np.roll(evt_arr,y_shift,axis=0)

        # Add Gaussian noise.
        if(self.add_noise):
            evt_arr = gaussnoise(evt_arr, mean=self.noise_mean, stdev=self.noise_sigma)

        # Add the relative incident positions to the shifts.
        err = [emnet.PIXEL_SIZE*x_shift + df_evt.xinc.values[0], emnet.PIXEL_SIZE*y_shift + df_evt.yinc.values[0]]

        # Construct the error matrix.
        SHIFTED_ERR_RANGE_MIN = emnet.PIXEL_ERR_RANGE_MIN - self.add_shift*emnet.PIXEL_SIZE
        SHIFTED_ERR_RANGE_MAX = emnet.PIXEL_ERR_RANGE_MAX + self.add_shift*emnet.PIXEL_SIZE

        xbin = int(emnet.ERR_SIZE*(err[0] - SHIFTED_ERR_RANGE_MIN)/(SHIFTED_ERR_RANGE_MAX - SHIFTED_ERR_RANGE_MIN))
        xbin = max(xbin,0)
        xbin = min(xbin,emnet.ERR_SIZE-1)

        ybin = int(emnet.ERR_SIZE*(err[1] - SHIFTED_ERR_RANGE_MIN)/(SHIFTED_ERR_RANGE_MAX - SHIFTED_ERR_RANGE_MIN))
        ybin = max(ybin,0)
        ybin = min(ybin,emnet.ERR_SIZE-1)

        err_ind = (ybin*emnet.ERR_SIZE) + xbin

        return evt_arr,err,err_ind

# Custom batch function
def my_collate(batch):

    data,target = [], []
    for item in batch:
        d,t = item[0], item[2]

        # Apply a random transformation.
        if(augment):
            d = rotate3D(d)
        #
        # # Pad all 0s to get a time dimension of tdim.
        # d = np.pad(d, [(0,ch_dim-d.shape[0]),(0,0),(0,0)])

        #print("final shapes are",d.shape,t.shape)
        data.append(d)
        target.append(t)

    #print("Max in batch is",np.max(data))
    data = torch.tensor(data).float().unsqueeze(1)
    target = torch.tensor(np.array(target)).long()

    return (data, target)


def my_collate_unet(batch):

    data,target = [], []
    for item in batch:
        d,t = item[0], item[1]

        # Apply a random transformation.
        if(augment):
            d = rotate3D(d)
        #
        # # Pad all 0s to get a time dimension of tdim.
        # d = np.pad(d, [(0,ch_dim-d.shape[0]),(0,0),(0,0)])

        #print("final shapes are",d.shape,t.shape)
        data.append(d)
        target.append(t)

    #print("Max in batch is",np.max(data))
    data = torch.tensor(data).float().unsqueeze(1)
    target = torch.tensor(target).float() #.unsqueeze(1)

    return (data, target)


class EMFrameDataset(Dataset):
    def __init__(self, emdset, nframes=1000, frame_size=576, nelec_mean=2927.294, nelec_sigma=70.531, noise_mean=0, noise_sigma=0, m_line=None, b_line=None, th_classical = 825, lside = -1):

        # Save some inputs for later use.
        self.emdset = emdset
        self.nframes = nframes
        self.frame_size = frame_size
        self.nelec_mean = nelec_mean
        self.nelec_sigma = nelec_sigma
        self.noise_mean = noise_mean
        self.noise_sigma = noise_sigma
        self.m_line = m_line
        self.b_line = b_line
        self.th_classical = th_classical
        self.lside = lside

        # Get the row and column indices.
        indices = np.indices((frame_size,frame_size))
        self.irows = indices[0]
        self.icols = indices[1]

    def __len__(self):
        return self.nframes

    def __getitem__(self, idx):

        # Create a random event (index does nothing, though could possibly be used as seed).
        frame = np.zeros([self.frame_size,self.frame_size])
        frame_truth = np.zeros(frame.shape)

        # Determine the number of electrons.
        nelec = 1 #int(np.random.normal(loc=self.nelec_mean,scale=self.nelec_sigma))

        # Determine the location of the light region, below (= 0), or above (= 1) the line.
        if(self.lside >= 0):
            light_region = self.lside
        else:
            light_region = np.random.randint(2)

        # Add all electrons to the event.
        iel = 0
        while(iel < nelec):
        #for iel in range(nelec):

            # Pick a random location in the frame for the electron.
            eloc = np.unravel_index(np.random.randint(frame.size),frame.shape)

            # If we have specified an edge, check whether we should throw the electron.
            if((self.m_line is not None and self.b_line is not None)):

                # Do not throw the electron in the dark region.
                irow = self.irows[eloc]
                icol = self.icols[eloc]
                if(((light_region == 0) and (irow < self.m_line*icol + self.b_line)) or ((light_region == 1) and (irow > self.m_line*icol + self.b_line))):
                    continue

            # Throw an electron.
            iel += 1

            # Pick a random event from the EM dataset.
            ievt = np.random.randint(len(self.emdset))
            evt_item = self.emdset[ievt]
            evt_arr = evt_item[0]

            # Add the electron to the frame.
            delta = int((emnet.EVT_SIZE-1)/2)  # the extent of the event from the central pixel
            ileft = max(eloc[0]-delta,0); delta_ileft = eloc[0] - ileft
            jleft = max(eloc[1]-delta,0); delta_jleft = eloc[1] - jleft

            iright = min(eloc[0]+delta+1,frame.shape[0]); delta_iright = iright - eloc[0]
            jright = min(eloc[1]+delta+1,frame.shape[1]); delta_jright = jright - eloc[1]

            frame[ileft:iright,jleft:jright] += evt_arr[delta-delta_ileft:delta+delta_iright,delta-delta_jleft:delta+delta_jright]

            # Add the electron to the truth array.
            frame_truth[eloc] = 1

        # Add the noise.
        if(self.noise_sigma > 0):
            frame = gaussnoise(frame, mean=self.noise_mean, stdev=self.noise_sigma)

        # Compute the distance matrix.
        dist = (self.m_line*self.icols - self.irows + self.b_line) / (self.m_line**2 + 1)

        # Create the edge truth.
        if(light_region == 0):
            #dist[self.irows <= self.m_line*self.icols + self.b_line] = 0
            edge_truth = self.irows >= self.m_line*self.icols + self.b_line
        else:
            #dist[self.irows >= self.m_line*self.icols + self.b_line] = 0
            edge_truth = self.irows <= self.m_line*self.icols + self.b_line

        # Create the threshold-based "truth".
        #th_truth = (frame > self.th_classical)
        edge_frame = frame * edge_truth
        th_truth = np.zeros(edge_frame.shape)
        th_truth[np.unravel_index(np.argmax(edge_frame),edge_truth.shape)] = 1

        # Store all the truth matrices in a single matrix.
        all_truth = []
        all_truth.append(frame_truth)
        all_truth.append(th_truth)
        all_truth.append(edge_truth)
        all_truth.append(dist)
        all_truth = np.array(all_truth)

        return frame,all_truth


def loss_edge(output, target, epoch = 0, sigma_dist = 1, w_edge = 100):
    output = output.squeeze(1)
    #print("target shape is",target.shape,"; output shape is",output.shape)

    th_truth = target[:,1,:,:]
    edge_truth = target[:,2,:,:]
    dist = target[:,3,:,:]

    # Define some Torch functions.
    sigmoid = torch.nn.Sigmoid()
    bce_loss = torch.nn.BCEWithLogitsLoss(reduce=False)

    # Modify the truth according to the epoch.
    # frac = min(epoch/500., 1.)
    # final_target = (1-frac)*th_truth + frac*sigmoid(output)

    final_target = th_truth

    # Compute the weights (tensor of shape [batchsize]).
    wts     = torch.sum(torch.exp(-(dist)**2/(2*sigma_dist**2))*th_truth,axis=(1,2))
    wt_norm = torch.sum(th_truth,axis=(1,2))
    wt_norm[wt_norm == 0] = 1
    wts /= wt_norm
    #wts[wts == 0] = 0.1

    # Zero-out the distance on the light side.
    dist_mod = torch.abs(dist*(edge_truth-1))

    # Compute the loss.
    #wts = torch.sum(torch.exp(-(dist)**2/(2*sigma_dist**2))*output,axis=0)
    #loss = torch.mean(torch.exp(-(dist)**2/(2*sigma_dist**2))*(bce_loss(output,th_truth) + w_edge*sigmoid(output)*(1-edge_truth)))
    #loss = torch.mean(torch.exp(-(dist)**2/(2*sigma_dist**2))*(bce_loss(output,final_target)))

    # --------------------------------------------------------------------------
    # BCE loss.
    loss_total = torch.sum(bce_loss(output,final_target),axis=(1,2))
    # --------------------------------------------------------------------------

    # --------------------------------------------------------------------------
    # Constrained loss (tensor of shape [batchsize])
    # loss_bce = torch.sum(bce_loss(output,final_target),axis=(1,2))
    # loss_sum_constraint = torch.abs(torch.sum(sigmoid(output),axis=(1,2)) - 1)
    # loss_edge_penalty = w_edge*torch.sum(sigmoid(output)*dist_mod,axis=(1,2))
    #
    # #loss_total = loss_bce + loss_edge_penalty
    # loss_total = loss_edge_penalty + loss_sum_constraint
    # print("-- BCE loss: {}".format(loss_bce))
    # print("-- edge loss: {}".format(loss_edge_penalty))
    # print("-- sum-constraint loss: {}".format(loss_sum_constraint))
    # --------------------------------------------------------------------------

    # Weight the loss.
    #loss_weighted = torch.mean(wts*loss_total)
    loss_weighted = torch.mean(loss_total)
    return loss_weighted


def train_unet(model, epoch, train_loader, optimizer, sigma_dist = 2):

    losses_epoch = []; accuracies_epoch = []
    for batch_idx, (data, target) in enumerate(train_loader):

        data, target = data.cuda(), target.cuda()
        data, target = Variable(data), Variable(target)

        #print("Target is",target)

        # Compute the final target.
        # final_target = th_truth * edge_truth
        # final_target = final_target.unsqueeze(1)

        output_score = model(data)
        #m = nn.BCEWithLogitsLoss(weight=wts)
        #loss = m(output_score,final_target)
        loss = loss_edge(output_score,target,epoch,w_edge = 1.0)

        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_value_(model.parameters(), 0.1)
        optimizer.step()

        maxvals = (output_score[:,0,:,:] > 0.9)
        correctvals = (maxvals == target[:,0,:,:])
        accuracy = correctvals.sum().float() / float(target[:,0,:,:].nelement())

        if batch_idx % 1 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\t score_max: {:.6f}\t score_min: {:.6f}; Accuracy {:.3f}'.format(
                epoch, batch_idx * len(data), len(train_loader.dataset),
                100. * batch_idx / len(train_loader), loss.data.item(), output_score.data.max(), output_score.data.min(), accuracy.data.item()))

        losses_epoch.append(loss.data.item())
        accuracies_epoch.append(accuracy.data.item())

    print("---EPOCH AVG TRAIN LOSS:",np.mean(losses_epoch),"ACCURACY:",np.mean(accuracies_epoch))
    with open("train.txt", "a") as ftrain:
        ftrain.write("{} {} {}\n".format(epoch,np.mean(losses_epoch),np.mean(accuracies_epoch)))

    return np.mean(losses_epoch)


def train(model, epoch, train_loader, optimizer):

    losses_epoch = []; accuracies_epoch = []
    for batch_idx, (data, target) in enumerate(train_loader):

        data, target = data.cuda(), target.cuda()
        data, target = Variable(data), Variable(target)
        optimizer.zero_grad()

        #print("Target is",target)

        output_score = model(data)
        m = nn.CrossEntropyLoss()
        loss = m(output_score,target)

        loss.backward()
        optimizer.step()

        maxvals = output_score.argmax(dim=1)
        correctvals = (maxvals == target)
        accuracy = correctvals.sum().float() / float(target.size(0))

        if batch_idx % 1 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\t score_max: {:.6f}\t score_min: {:.6f}; Accuracy {:.3f}'.format(
                epoch, batch_idx * len(data), len(train_loader.dataset),
                100. * batch_idx / len(train_loader), loss.data.item(), output_score.data.max(), output_score.data.min(), accuracy.data.item()))

        losses_epoch.append(loss.data.item())
        accuracies_epoch.append(accuracy.data.item())

    print("---EPOCH AVG TRAIN LOSS:",np.mean(losses_epoch),"ACCURACY:",np.mean(accuracies_epoch))
    with open("train.txt", "a") as ftrain:
        ftrain.write("{} {} {}\n".format(epoch,np.mean(losses_epoch),np.mean(accuracies_epoch)))

    return np.mean(losses_epoch)

def val(model, epoch, val_loader):

    losses_epoch = []; accuracies_epoch = []
    for batch_idx, (data, target) in enumerate(val_loader):

        data, target = data.cuda(), target.cuda()
        data, target = Variable(data), Variable(target)

        output_score = model(data)
        m = nn.CrossEntropyLoss()
        loss = m(output_score,target)

        maxvals = output_score.argmax(dim=1)
        correctvals = (maxvals == target)
        accuracy = correctvals.sum().float() / float(target.size(0))

        if batch_idx % 1 == 0:
            print('--Val Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\t score_max: {:.6f}\t score_min: {:.6f}; Accuracy {:.3f}'.format(
                epoch, batch_idx * len(data), len(val_loader.dataset),
                100. * batch_idx / len(val_loader), loss.data.item(), output_score.data.max(), output_score.data.min(), accuracy.data.item()))

        losses_epoch.append(loss.data.item())
        accuracies_epoch.append(accuracy.data.item())

    print("---EPOCH AVG VAL LOSS:",np.mean(losses_epoch),"ACCURACY:",np.mean(accuracies_epoch))
    with open("val.txt", "a") as fval:
        fval.write("{} {} {}\n".format(epoch,np.mean(losses_epoch),np.mean(accuracies_epoch)))

    return np.mean(losses_epoch)


# ------------------------------------------------------------------------------
# OLD CODE

# Sum the 3x3 square within the array containing the specified index and its neighbors.
# Set all neighbors included in the sum to 0 if the remove option is set.
def sum_neighbors(arr,ind,remove = False):

    # Start with the central pixel.
    sum = arr[ind]
    if(remove): arr[ind] = 0

    # Determine which neighbors exist.
    left_neighbor  = (ind[1]-1) >= 0
    right_neighbor = (ind[1]+1) < arr.shape[0]
    upper_neighbor = (ind[0]-1) >= 0
    lower_neighbor = (ind[0]+1) < arr.shape[1]

    # Add the 4 side-neighboring pixels to the sum.
    if(left_neighbor):
        sum += arr[ind[0],ind[1]-1]
        if(remove): arr[ind[0],ind[1]-1] = 0
    if(right_neighbor):
        sum += arr[ind[0],ind[1]+1]
        if(remove): arr[ind[0],ind[1]+1] = 0
    if(upper_neighbor):
        sum += arr[ind[0]-1,ind[1]]
        if(remove): arr[ind[0]-1,ind[1]] = 0
    if(lower_neighbor):
        sum += arr[ind[0]+1,ind[1]]
        if(remove): arr[ind[0]+1,ind[1]] = 0

    # Add the 4 diagonal neighbors to the sum.
    if(left_neighbor and upper_neighbor):
        sum += arr[ind[0]-1,ind[1]-1]
        if(remove): arr[ind[0]-1,ind[1]-1] = 0
    if(right_neighbor and upper_neighbor):
        sum += arr[ind[0]-1,ind[1]+1]
        if(remove): arr[ind[0]-1,ind[1]+1] = 0
    if(left_neighbor and lower_neighbor):
        sum += arr[ind[0]+1,ind[1]-1]
        if(remove): arr[ind[0]+1,ind[1]-1] = 0
    if(right_neighbor and lower_neighbor):
        sum += arr[ind[0]+1,ind[1]+1]
        if(remove): arr[ind[0]+1,ind[1]+1] = 0

    return sum

# if(self.add_noise):
#
#     max_before_noise = np.unravel_index(evt_arr.argmax(),evt_arr.shape)
#
#     # Add the noise.
#     evt_arr = gaussnoise(evt_arr, mean=self.noise_mean, stdev=self.noise_sigma)
#
#     # Make a copy of the array that we can safely modify.
#     evt_arr_temp = np.copy(evt_arr)
#
#     # ------------------------------------------------------------------------
#     # Determine the new maximum, as the largest 3x3 sum around a maximum pixel.
#     # 1. Find the initial maximum and compute sum of surrounding 3x3 region
#     # 2. Remove 3x3 region summed in step 1 from consideration for being maximum pixel
#     # 3. Find a new maximum and compute the 3x3 sum about this new maximum
#     # 4. Remove 3x3 region summed in step 2 from consideration for being maximum pixel
#     # 5. If the 3x3 region sum from step 3 is greater than that of the initial maximum from step 1, replace the initial maximum with the new maximum
#     # 6. Repeat steps 3-5 until the new maximum is <= 0 or the region sum is less than the initial region sum
#
#     # Get the initial maximum and neighbor sum, removing these neighbors from consideration for the next maximum.
#     max_init   = np.unravel_index(evt_arr_temp.argmax(),evt_arr.shape)
#     nbsum_init = sum_neighbors(evt_arr_temp,max_init,remove=True)
#     found = False
#     while(not found):
#
#         # Get the next maximum.
#         max_current   = np.unravel_index(evt_arr_temp.argmax(),evt_arr.shape)
#         nbsum_current = sum_neighbors(evt_arr,max_current,remove=False)        # note: the sum should be from the original (unmodified) array
#
#         # A maximum of less than or equal to zero means we are done, and we should keep the previous maximum.
#         if(evt_arr[max_current] <= 0):
#             found = True
#
#         # If the current neighbor sum is greater than that of the initial maximum, replace the initial maximum with the current one.
#         elif(nbsum_current > nbsum_init):
#             sum_neighbors(evt_arr_temp,max_current,remove=True)  # remove the neighbors of the current maximum
#
#             # Replace the initial maximum and its neighbor sum.
#             #print("Replacing init maximum at",max_init,"with max_current",max_current)
#             max_init = max_current
#             nbsum_init = nbsum_current
#
#         # Otherwise keep the current initial maximum.
#         else:
#             found = True
#     # ------------------------------------------------------------------------
#
#     # Calculate the shift.
#     x_shift = (max_init[1] - max_before_noise[1])
#     y_shift = (max_init[0] - max_before_noise[0])
#
#     # Shift to the new maximum.
#     evt_arr = np.roll(evt_arr,x_shift,axis=1)
#     evt_arr = np.roll(evt_arr,y_shift,axis=0)
