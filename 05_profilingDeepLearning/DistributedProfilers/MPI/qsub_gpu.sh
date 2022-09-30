#!/bin/bash
##COBALT -q training -A SDL_Workshop -n 1 -t 10 -O MPI_single_node

module load conda/2021-09-22
conda activate

export LD_PRELOAD=/lus/theta-fs0/software/datascience/thetagpu/hpctw/lib/libmpitrace.so

NODES=$(cat $COBALT_NODEFILE | wc -l)

mpirun -n $NPROC python ../tensorflow2_mnist.py --device gpu  >& gpu_n$NPROC.out


