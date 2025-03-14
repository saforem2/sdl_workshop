from __future__ import print_function
import os
os.environ['IBV_FORK_SAFE']='1'
import argparse
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms
import torch.utils.data.distributed
#Horovod: import horovod
import horovod.torch as hvd
import time
import torchvision


# Training settings
parser = argparse.ArgumentParser(description='PyTorch MNIST Example')
parser.add_argument('--batch_size', type=int, default=64, metavar='N',
                    help='input batch size for training (default: 16)')
parser.add_argument('--test-batch-size', type=int, default=64, metavar='N',
                    help='input batch size for testing (default: 1000)')
parser.add_argument('--epochs', type=int, default=32, metavar='N',
                    help='number of epochs to train (default: 32)')
parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                    help='learning rate (default: 0.01)')
parser.add_argument('--momentum', type=float, default=0.5, metavar='M',
                    help='SGD momentum (default: 0.5)')
parser.add_argument('--seed', type=int, default=42, metavar='S',
                    help='random seed (default: 42)')
parser.add_argument('--log-interval', type=int, default=100, metavar='N',
                    help='how many batches to wait before logging training status')
parser.add_argument('--fp16-allreduce', action='store_true', default=False,
                    help='use fp16 compression during allreduce')
parser.add_argument('--device', default='cpu',
                    help='Wheter this is running on cpu or gpu')
parser.add_argument('--wandb', action='store_true', 
                    help='whether to use wandb to log data')
parser.add_argument('--project', default="sdl-pytorch-cifar10", type=str)
parser.add_argument('--num_threads', default=0, help='set number of threads per worker', type=int)
parser.add_argument('--num_workers', default=1, help='set number of io workers', type=int)

args = parser.parse_args()



args.cuda = args.device.find("gpu")!=-1
# Horovod: initialize library.
hvd.init()

if args.wandb and hvd.rank()==0:
    try:
        import wandb
        wandb.init(project=args.project)
    except:
        args.wandb = False
    config = wandb.config          # Initialize config
    config.batch_size = args.batch_size         # input batch size for training (default: 64)
    config.test_batch_size = args.test_batch_size    # input batch size for testing (default: 1000)
    config.epochs = args.epochs            # number of epochs to train (default: 10)
    config.lr = args.lr              # learning rate (default: 0.01)
    config.momentum = args.momentum         # SGD momentum (default: 0.5) 
    config.device = args.device        # disables CUDA training
    config.seed = args.seed               # random seed (default: 42)
    config.log_interval = args.log_interval     # how many batches to wait before logging training status
    config.num_workers = hvd.size()

torch.manual_seed(args.seed)
print("Horovod: I am worker %s of %s." %(hvd.rank(), hvd.size()))
if args.device.find("gpu")!=-1:
    # Horovod: pin GPU to local rank.
    torch.cuda.set_device(hvd.local_rank())
    torch.cuda.manual_seed(args.seed)
if (args.num_threads!=0):
    torch.set_num_threads(args.num_threads)

if hvd.rank()==0:
    print("Torch Thread setup: ")
    print(" Number of threads: ", torch.get_num_threads())
#    print(" Number of inter_op threads: ", torch.get_num_interop_threads())
normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])
kwargs = {'num_workers': args.num_workers, 'pin_memory': True} if args.device.find("gpu")!=-1 else {}


transform = transforms.Compose(
    [transforms.ToTensor(),
     transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

train_dataset = \
    datasets.CIFAR10('datasets/', train=True, download=True,
                     transform=transform,)

# Horovod: use DistributedSampler to partition the training data.
train_sampler = torch.utils.data.distributed.DistributedSampler(
    train_dataset, num_replicas=hvd.size(), rank=hvd.rank())
train_loader = torch.utils.data.DataLoader(
    train_dataset, batch_size=args.batch_size, sampler=train_sampler, **kwargs)

test_dataset = \
    datasets.CIFAR10('datasets', train=False, transform=transform)

# Horovod: use DistributedSampler to partition the test data.
test_sampler = torch.utils.data.distributed.DistributedSampler(
    test_dataset, num_replicas=hvd.size(), rank=hvd.rank())
test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=args.test_batch_size,
                                          sampler=test_sampler, **kwargs)


import torchvision.models as models
NUM_CLASSES = 10
class AlexNet(nn.Module):
    def __init__(self, num_classes=NUM_CLASSES):
        super(AlexNet, self).__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),
            nn.Conv2d(64, 192, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),
            nn.Conv2d(192, 384, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(384, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),
        )
        self.classifier = nn.Sequential(
            nn.Dropout(),
            nn.Linear(256 * 2 * 2, 4096),
            nn.ReLU(inplace=True),
            nn.Dropout(),
            nn.Linear(4096, 4096),
            nn.ReLU(inplace=True),
            nn.Linear(4096, num_classes),
        )

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), 256 * 2 * 2)
        x = self.classifier(x)
        return x
#model = torchvision.models.resnet50(num_classes=10)
model = AlexNet(num_classes=10)
if args.wandb and hvd.rank()==0:
    wandb.watch(model)

if args.device.find("gpu")!=-1:
    # Move model to GPU.
    model.cuda()
criterion = nn.CrossEntropyLoss()
# Horovod: scale learning rate by the number of GPUs.
optimizer = optim.SGD(model.parameters(), lr=args.lr * hvd.size(),
                      momentum=args.momentum)

# Horovod: broadcast parameters & optimizer state.
hvd.broadcast_parameters(model.state_dict(), root_rank=0)
hvd.broadcast_optimizer_state(optimizer, root_rank=0)

# Horovod: (optional) compression algorithm.
compression = hvd.Compression.fp16 if args.fp16_allreduce else hvd.Compression.none

# Horovod: wrap optimizer with DistributedOptimizer.
optimizer = hvd.DistributedOptimizer(optimizer,
				     named_parameters=model.named_parameters(),
				     compression=compression)


def train(epoch):
    model.train()
    # Horovod: set epoch to sampler for shuffling.
    train_sampler.set_epoch(epoch)
    running_loss = torch.tensor(0.0)
    training_acc = torch.tensor(0.0)
    if args.device == "gpu":
        running_loss = running_loss.cuda()
        training_acc = training_acc.cuda()
    for batch_idx, (data, target) in enumerate(train_loader):
        if args.cuda:
            data, target = data.cuda(), target.cuda()
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)
        #loss = F.nll_loss(output, target)
        loss.backward()
        optimizer.step()
        pred = output.data.max(1, keepdim=True)[1]
        training_acc += pred.eq(target.data.view_as(pred)).cpu().float().sum()
        running_loss += loss.item()
        if batch_idx % args.log_interval == 0:
            # Horovod: use train_sampler to determine the number of examples in
            # this worker's partition.
            print('[{}] Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(hvd.rank(), 
                epoch, batch_idx * len(data), len(train_sampler),
                100. * batch_idx / len(train_loader), loss.item()/args.batch_size))
    running_loss = running_loss / len(train_sampler)
    training_acc = training_acc / len(train_sampler)
    loss_avg = metric_average(running_loss, 'running_loss')
    training_acc = metric_average(training_acc, 'training_acc')
    if hvd.rank()==0: print("Training set: Average loss: {:.4f}, Accuracy: {:.2f}%".format(loss_avg, training_acc*100))
    return loss_avg, training_acc 

def metric_average(val, name):
    tensor = torch.tensor(val)
    avg_tensor = hvd.allreduce(tensor, name=name)
    return avg_tensor.item()


def test():
    model.eval()
    test_loss = torch.tensor(0.0)
    test_accuracy = torch.tensor(0.0)
    if args.device == "gpu":
        test_loss = test_loss.cuda()
        test_accuracy = test_accuracy.cuda()
    for data, target in test_loader:
        if args.cuda:
            data, target = data.cuda(), target.cuda()
        output = model(data)
        # sum up batch loss
        #test_loss += F.nll_loss(output, target, size_average=False).item()
        test_loss += criterion(output, target).item()
        # get the index of the max log-probability
        pred = output.data.max(1, keepdim=True)[1]
        test_accuracy += pred.eq(target.data.view_as(pred)).cpu().float().sum()

    # Horovod: use test_sampler to determine the number of examples in
    # this worker's partition.
    test_loss /= len(test_sampler)
    test_accuracy /= len(test_sampler)

    # Horovod: average metric values across workers.
    test_loss = metric_average(test_loss, 'avg_loss')
    test_accuracy = metric_average(test_accuracy, 'avg_accuracy')

    # Horovod: print output only on first rank.
    if hvd.rank() == 0:
        print('Test set: Average loss: {:.4f}, Accuracy: {:.2f}%\n'.format(
            test_loss, 100. * test_accuracy))
    return test_loss, test_accuracy

t0 = time.time()
for epoch in range(1, args.epochs + 1):
    tt0 = time.time()
    training_loss, training_acc = train(epoch)
    test_loss, test_acc = test()
    tt1 = time.time()
    if hvd.rank()==0:
        print("Epoch - %d time: %s seconds" %(epoch, tt1 - tt0))
    if (hvd.rank()==0 and args.wandb):
        wandb.log({"time_per_epoch": tt1 - tt0, 
            "train_loss": training_loss, "train_acc": training_acc, 
            "test_loss": test_loss, "test_acc":test_acc}, step=epoch)
t1 = time.time()
if hvd.rank()==0:
    print("Total training time: %s seconds" %(t1 - t0))
