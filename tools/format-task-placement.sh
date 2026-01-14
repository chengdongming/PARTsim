#!/bin/bash

(
    set -e

    # Usage: pipeline the output of ./build/rtsim/rtsim to this program
    sed '/Run #/,$!d' |
        sed 's#MRTKernel::printstate(), time ##' |
        sed 's#CPU_\([a-z_\-]\+\)_\([[:digit:]]\+\)# CPU_\1 CORE-\2 #g' |
        sed 's#CPU_# ISLAND-#g' |
        sed 's# 0 # idle #g' |
        tr -s ' :\t' '\t' |
        column -t
)
