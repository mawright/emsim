"""
emnet.py
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

netdebug = False
xdim = ydim = 21
err_size = 10
chi = 16

class FCNet(nn.Module):

    def __init__(self):
        super(FCNet, self).__init__()
        self.fc1 = nn.Linear(xdim*ydim, 256)
        self.fc2 = nn.Linear(1024, 256)
        self.fc3 = nn.Linear(256, err_size*err_size)
        self.drop1 = nn.Dropout(p=0.5)
        self.relu = nn.ReLU()
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):

        if(netdebug): print("Init:",x.shape)
        x = torch.flatten(x, start_dim=1)

        if(netdebug): print("Flatten:",x.shape)
        x = self.fc1(x)

        # if(netdebug): print("FC1:",x.shape)
        # x = self.sigmoid(x)
        # x = self.drop1(x)
        # x = self.fc2(x)

        if(netdebug): print("FC2:",x.shape)
        x = self.sigmoid(x)
        x = self.drop1(x)
        x = self.fc3(x)

        if(netdebug): print("FC3:",x.shape)
        x = self.sigmoid(x)

        return x

class basicCNN(nn.Module):
    def __init__(self):
        super(basicCNN, self).__init__()

        self.conv1 = nn.Conv2d(1, chi, 4, padding=1)
        self.bn1   = nn.BatchNorm2d(chi)
        self.conv2 = nn.Conv2d(chi, chi*2, 2, padding=1)
        self.bn2   = nn.BatchNorm2d(chi*2)
        self.conv3 = nn.Conv2d(chi*2, chi*4, 2, padding=1)
        self.bn3   = nn.BatchNorm2d(chi*4)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.pool4 = nn.MaxPool2d(4, 4)
        self.fc = nn.Linear(chi*16, err_size*err_size)
        self.drop1 = nn.Dropout(p=0.5)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def forward(self, x):
        if(netdebug): print(x.shape)
        x = self.pool4(self.bn1(F.relu(self.conv1(x))))
        if(netdebug): print(x.shape)
        x = self.pool2(self.bn2(F.relu(self.conv2(x))))
        if(netdebug): print(x.shape)
        x = self.pool2(self.bn3(F.relu(self.conv3(x))))
        if(netdebug): print(x.shape)
        x = x.view(-1, chi*16 * 1)
        if(netdebug): print(x.shape)
        x = self.drop1(x)
        x = self.fc(x)


        return x
