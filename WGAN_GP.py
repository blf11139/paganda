import utils, torch, time, os, pickle
import numpy as np
import torch.nn as nn
import torch.optim as optim
from torch.autograd import grad
from dataloader import dataloader
import torchvision.datasets as datasets
import torchvision.transforms as transforms
from torch.utils.data.sampler import SubsetRandomSampler
import torchvision.utils as tvutils
import torch.utils as tutils
from torch.autograd import Variable
import image_slicer
import pickle_loader as pl

class generator(nn.Module):
    # Network Architecture is exactly same as in infoGAN (https://arxiv.org/abs/1606.03657)
    # Architecture : FC1024_BR-FC7x7x128_BR-(64)4dc2s_BR-(1)4dc2s_S
    def __init__(self, input_dim=100, output_dim=1, input_size=32):
        super(generator, self).__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.input_size = input_size

        self.fc = nn.Sequential(
            nn.Linear(self.input_dim, 1024),
            nn.BatchNorm1d(1024),
            nn.ReLU(),
            nn.Linear(1024, 128 * (self.input_size // 4) * (self.input_size // 4)),
            nn.BatchNorm1d(128 * (self.input_size // 4) * (self.input_size // 4)),
            nn.ReLU(),
        )
        self.deconv = nn.Sequential(
            nn.ConvTranspose2d(128, 64, 4, 2, 1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.ConvTranspose2d(64, self.output_dim, 4, 2, 1),
            nn.Tanh(),
        )
        utils.initialize_weights(self)

    def forward(self, input):
        x = self.fc(input)
        x = x.view(-1, 128, (self.input_size // 4), (self.input_size // 4))
        x = self.deconv(x)

        return x

class discriminator(nn.Module):
    # Network Architecture is exactly same as in infoGAN (https://arxiv.org/abs/1606.03657)
    # Architecture : (64)4c2s-(128)4c2s_BL-FC1024_BL-FC1_S
    def __init__(self, input_dim=1, output_dim=1, input_size=32):
        super(discriminator, self).__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.input_size = input_size

        self.conv = nn.Sequential(
            nn.Conv2d(self.input_dim, 64, 4, 2, 1),
            nn.LeakyReLU(0.2),
            nn.Conv2d(64, 128, 4, 2, 1),
            nn.BatchNorm2d(128),
            nn.LeakyReLU(0.2),
        )
        self.fc = nn.Sequential(
            nn.Linear(128 * (self.input_size // 4) * (self.input_size // 4), 1024),
            nn.BatchNorm1d(1024),
            nn.LeakyReLU(0.2),
            nn.Linear(1024, self.output_dim),
            # nn.Sigmoid(),
        )
        utils.initialize_weights(self)

    def forward(self, input):
        x = self.conv(input)
        x = x.view(-1, 128 * (self.input_size // 4) * (self.input_size // 4))
        x = self.fc(x)

        return x

class WGAN_GP(object):
    def __init__(self, args):
        # parameters
        self.epoch = args.epoch
        self.sample_num = 100
        self.batch_size = args.batch_size
        self.save_dir = args.save_dir
        self.result_dir = args.result_dir
        self.datasetname = args.dataset
        self.log_dir = args.log_dir
        self.gpu_mode = args.gpu_mode
        self.model_name = args.gan_type
        self.input_size = args.input_size
        self.folder=args.folder
        self.z_dim = 62
        self.lambda_ = 10
        self.n_critic = 5               # the number of iterations of the critic per generator iteration
        self.repeat=args.repeat
        # load dataset
        #self.dataset=pl.generate_random()
        if self.repeat==0:
            self.dataset=pl.read_from_data_for_k_folder('pickle_seed.out',self.folder)
        else:
            self.dataset=pl.read_from_data_for_k_folder_add_size('result_collection',self.folder,32+(self.repeat-1)*16,16,self.repeat)
        #self.dataset=pl.gather_trained_data('results_GAN_Game','transformed_array.out',48,16)
        #print(self.dataset[0])
        #print(self.dataset)
        #self.data_loader = dataloader(self.dataset, self.input_size, self.batch_size)
        #self.dataset = datasets.ImageFolder(root='data/'+self.datasetname, transform=transforms.Compose([
        #                               transforms.Resize(self.input_size),
        #                               transforms.CenterCrop(self.input_size),
        #                               transforms.ToTensor(),
        #                               transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        #                               ]))
        self.data_loader = tutils.data.DataLoader(self.dataset, batch_size=self.batch_size, shuffle=True, drop_last=True)
        #self.data_loader = dataloader(self.dataset, self.input_size, self.batch_size)
        #indices = list(range(50000))
        #subset_indices= indices[:1000]
        #train_sampler = SubsetRandomSampler(subset_indices)
        #self.data_loader = torch.utils.data.DataLoader(self.dataset, batch_size=64, sampler=train_sampler,num_workers=1,drop_last=True)
        data = self.data_loader.__iter__().__next__()[0]

        # networks init
        self.G = generator(input_dim=self.z_dim, output_dim=data.shape[1], input_size=self.input_size)
        self.D = discriminator(input_dim=data.shape[1], output_dim=1, input_size=self.input_size)
        self.G_optimizer = optim.Adam(self.G.parameters(), lr=args.lrG, betas=(args.beta1, args.beta2))
        self.D_optimizer = optim.Adam(self.D.parameters(), lr=args.lrD, betas=(args.beta1, args.beta2))

        if self.gpu_mode:
            self.G.cuda()
            self.D.cuda()

        print('---------- Networks architecture -------------')
        utils.print_network(self.G)
        utils.print_network(self.D)
        print('-----------------------------------------------')
        if self.repeat==1:
            self.G.load_state_dict(torch.load(args.netG_path))
            self.D.load_state_dict(torch.load(args.netD_path))
        # fixed noise
        self.sample_z_ = torch.rand((self.batch_size, self.z_dim))
        if self.gpu_mode:
            self.sample_z_ = self.sample_z_.cuda()

    def train(self):
        self.train_hist = {}
        self.train_hist['D_loss'] = []
        self.train_hist['G_loss'] = []
        self.train_hist['per_epoch_time'] = []
        self.train_hist['total_time'] = []

        self.y_real_, self.y_fake_ = torch.ones(self.batch_size, 1), torch.zeros(self.batch_size, 1)
        if self.gpu_mode:
            self.y_real_, self.y_fake_ = self.y_real_.cuda(), self.y_fake_.cuda()

        self.D.train()
        print('training start!!')
        start_time = time.time()
        for epoch in range(self.epoch):
            self.G.train()
            epoch_start_time = time.time()
            for iter, (x_,_) in enumerate(self.data_loader):
                if iter == self.data_loader.dataset.__len__() // self.batch_size:
                    break
                #print(x_.size())
                z_ = torch.rand((self.batch_size, self.z_dim))
                if self.gpu_mode:
                    x_, z_ = x_.cuda(), z_.cuda()

                # update D network
                self.D_optimizer.zero_grad()

                D_real = self.D(x_)
                D_real_loss = -torch.mean(D_real)

                G_ = self.G(z_)
                D_fake = self.D(G_)
                D_fake_loss = torch.mean(D_fake)

                # gradient penalty
                alpha = torch.rand((self.batch_size, 1, 1, 1))
                if self.gpu_mode:
                    alpha = alpha.cuda()

                x_hat = alpha * x_.data + (1 - alpha) * G_.data
                x_hat.requires_grad = True

                pred_hat = self.D(x_hat)
                if self.gpu_mode:
                    gradients = grad(outputs=pred_hat, inputs=x_hat, grad_outputs=torch.ones(pred_hat.size()).cuda(),
                                 create_graph=True, retain_graph=True, only_inputs=True)[0]
                else:
                    gradients = grad(outputs=pred_hat, inputs=x_hat, grad_outputs=torch.ones(pred_hat.size()),
                                     create_graph=True, retain_graph=True, only_inputs=True)[0]

                gradient_penalty = self.lambda_ * ((gradients.view(gradients.size()[0], -1).norm(2, 1) - 1) ** 2).mean()

                D_loss = D_real_loss + D_fake_loss + gradient_penalty

                D_loss.backward()
                self.D_optimizer.step()

                if ((iter+1) % self.n_critic) == 0:
                    # update G network
                    self.G_optimizer.zero_grad()

                    G_ = self.G(z_)
                    D_fake = self.D(G_)
                    G_loss = -torch.mean(D_fake)
                    self.train_hist['G_loss'].append(G_loss.item())

                    G_loss.backward()
                    self.G_optimizer.step()

                    self.train_hist['D_loss'].append(D_loss.item())

                if ((iter + 1) % 20) == 0:
                    print("Epoch: [%2d] [%4d/%4d] D_loss: %.8f, G_loss: %.8f" %
                          ((epoch + 1), (iter + 1), self.data_loader.dataset.__len__() // self.batch_size, D_loss.item(), G_loss.item()))

            self.train_hist['per_epoch_time'].append(time.time() - epoch_start_time)
            #with torch.no_grad():
            #    self.visualize_results((epoch+1))
    
        self.train_hist['total_time'].append(time.time() - start_time)
        print("Avg one epoch time: %.2f, total %d epochs time: %.2f" % (np.mean(self.train_hist['per_epoch_time']),
              self.epoch, self.train_hist['total_time'][0]))
        print("Training finish!... save training results")
        
        self.save()
        torch.save(self.G.state_dict(), "%s/generator_repeat_%d.pth" % ("distributed_model_"+(str)(self.folder), self.repeat))
        torch.save(self.D.state_dict(), "%s/discriminator_repeat_%d.pth" %("distributed_model_"+(str)(self.folder),self.repeat))
        #torch.save(self.D.state_dict(),"%s/discriminator_epoch_%03d.pth" % ("model_result_distributed_"+(str)(self.datasetname.split('_')[1]), epoch))
        #torch.save(self.G.state_dict(), "%s/generator_epoch_%03d.pth" % ("model_result_distributed_"+(str)(self.datasetname.split('_')[1]), epoch))
        #utils.generate_animation(self.result_dir + '/' + self.datasetname + '/' + self.model_name + '/' + self.model_name,
                                 #self.epoch)
        #utils.loss_plot(self.train_hist, os.path.join(self.save_dir, self.datasetname, self.model_name), self.model_name)

    def visualize_results(self, epoch, fix=True):
        self.G.eval()

        if not os.path.exists(self.result_dir + '/' + self.datasetname + '/' + self.model_name):
            os.makedirs(self.result_dir + '/' + self.datasetname + '/' + self.model_name)

        tot_num_samples = min(self.sample_num, self.batch_size)
        image_frame_dim = int(np.floor(np.sqrt(tot_num_samples)))

        if fix:
            """ fixed noise """
            samples = self.G(self.sample_z_)
        else:
            """ random noise """
            sample_z_ = torch.rand((self.batch_size, self.z_dim))
            if self.gpu_mode:
                sample_z_ = sample_z_.cuda()

            samples = self.G(sample_z_)

        if self.gpu_mode:
            samples = samples.cpu().data.numpy().transpose(0, 2, 3, 1)
        else:
            samples = samples.data.numpy().transpose(0, 2, 3, 1)
        #print(samples.shape)
        #file_write=open('results.out','a')
       
        for img in range(self.batch_size):
            file_write=open('./results_GAN_Game_'+(str)(self.folder)+'/results_'+(str)(img)+'.out','w')
            for dim in range(3):
                for i in range(20):
                    for j in range(10):
                        file_write.write((str)(samples[img][i][j][dim])+' ')
                file_write.write('\n')
            file_write.close()
        #samples = (samples + 1) / 2
        #utils.save_images(samples[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
        #                  self.result_dir + '/' + self.datasetname + '/' + self.model_name + '/' + self.model_name + '_epoch%03d' % epoch + '.png')
        #if epoch==self.epoch:
        #    tiles = image_slicer.slice(self.result_dir + '/' + self.datasetname + '/' + self.model_name + '/' +'/WGAN_GP_epoch{0}.png'.format(self.epoch), 64, save=False)		
        #    image_slicer.save_tiles(tiles, directory=self.result_dir + '/' + self.datasetname + '/' + self.model_name +'/sliced',prefix='fake_samples_')


    def save(self):
        save_dir = os.path.join(self.save_dir, self.datasetname, self.model_name)

        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        torch.save(self.G.state_dict(), os.path.join(save_dir, self.model_name + '_G.pkl'))
        torch.save(self.D.state_dict(), os.path.join(save_dir, self.model_name + '_D.pkl'))

        with open(os.path.join(save_dir, self.model_name + '_history.pkl'), 'wb') as f:
            pickle.dump(self.train_hist, f)

    def load(self):
        save_dir = os.path.join(self.save_dir, self.datasetname, self.model_name)

        self.G.load_state_dict(torch.load(os.path.join(save_dir, self.model_name + '_G.pkl')))
        self.D.load_state_dict(torch.load(os.path.join(save_dir, self.model_name + '_D.pkl')))
