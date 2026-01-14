#!/bin/bash

mydir=$(realpath $(dirname $0))

# Maximum number of tasks per taskset
NMAX=10
# Number of tasks (with vtime in the future) to be killed at random time
K=3
# Utot at beginning of experiment
U=0.9
# Number of repetitions
R=1000

LD_LIBRARY_PATH="$LD_LIBRARY_PATH:$mydir/../../"

for U in 0.90 0.95 0.99; do
    for K in 1 2 3; do
        (
            outdir=out-$U-$K
            echo "Running with U=$U, K=$K (outdir=$outdir)..."
            rm -rf $outdir
            mkdir -p $outdir
            cd $outdir
            e=0
            for ((i=0; i<$R; i++)); do
                $mydir/edf-death-birth -n $NMAX -k $K -u $U -f 1.0 > log.txt
                if grep miss trace.txt > /dev/null; then
                    echo "i=$i: ERR";
                    e=$[$e+1]
                else
                    echo "i=$i: OK";
                fi;
                mv log.txt log-$i.txt
                mv trace.txt trace-$i.txt
            done

            echo

            if [ $e -eq 0 ]; then
                echo "U=$U, K=$K: Ok, no misses detected!"
            else
                echo "U=$U, K=$K: Error, $e misses detected!"
                exit 1
            fi
            cd ..
        ) &
    done
done

echo "Waiting for termination of all children..."
wait
