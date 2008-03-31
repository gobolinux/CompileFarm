#!/usr/bin/env python

#
# GoboLinux CompileFarm test interface
# (C) 2008 Lucas C. Villa Real. Released under the GNU GPL version 2.
#

import socket, sys

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(('localhost', 2112))

while True :
	sys.stdout.write('> ')
	line = sys.stdin.readline()
	cmd = line.strip('\n\r')
	if len(cmd) == 0 :
		continue

	client.send(cmd)
	print client.recv(4096)
	if cmd == 'quit' :
		break
