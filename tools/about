#!/usr/bin/python

import sys

def main():
	if len(sys.argv) < 3 or ( len(sys.argv) == 2 and (sys.argv[1] == 'help' or sys.argv[1] == '-h') ):
		print 'Example: about task_1 trace.txt'
		print 'to show only lines regarding task_1 in file trace.txt'
		return

	filename 		= sys.argv[2]
	toBeSearched 	= sys.argv[1]

	print 'Looking for ' + toBeSearched + ' in ' + filename

	with open(filename) as f:
	    for line in f:
	        if toBeSearched in line:
	        	print line

if __name__ == '__main__':
	main()
