#!/bin/bash

(
    compile="cc --std=c++17 -Ilibmetasim/include/ -I rtsim/cmdarg/include/public -I rtsim/cmdarg/include/private -Ilibrtsim/include/ -c"

    green="$(tput setaf 2)"
    red="$(tput setaf 1)"
    normal="$(tput sgr0)"

    greentick="${green}\u2714${normal}"
    redcross="${red}\u2717${normal}"

    headers=()

    FOUT=/dev/stdout

    if [ $# -gt 0 ] ; then
        headers=($@)
    else
        headers=( $(find . -name '*.hpp') )
        FOUT=/dev/null
    fi

    for h in ${headers[*]}; do
        ${compile} "$h" -o /tmp/tmp.o &>$FOUT &&
            printf " [${greentick}] COMPILATION SUCCESS: %-20s\n" "$h" ||
            printf " [${redcross}] COMPILATION FAILURE: %-20s\n" "$h"
    done
)

rm -f /tmp/tmp.o
