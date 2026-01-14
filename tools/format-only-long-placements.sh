#!/bin/bash

(
    set -e

    # Usage: pipeline the output of format-task-placement to this program

    oldstamp=''
    oldline=''

    while read -r line; do
        tstamp=$(echo "$line" | cut -d' ' -f 1)

        if [ "$oldstamp" != "$tstamp" ] && [ "$oldline" != "" ]; then
            echo "$oldline"
        fi

        oldstamp="$tstamp"
        oldline="$line"
    done

    echo "$oldline"
)
