#!/bin/bash

(
    island=$1

    gnuplot_cmds=(
        'set terminal png size 1000,1000;'
        'set output "prova.png";'
        'plot "/dev/stdin" using 0:1 with lines'
    )

    paste power_${island}*.txt |
        sed 's#Current Power Consumption:##g' |
        sed 's#\t#+#g' |
        bc -l |
        gnuplot -p -e "${gnuplot_cmds[*]}"
)
