#!/usr/bin/env python

#
# GoboLinux CompileFarm Slave
# (C) 2008 Lucas C. Villa Real. Released under the GNU GPL version 2.
#

import os, sys, threading, mutex, time, popen2, struct
from socket import *

eval(compile(open('CompileFarm.conf').read(), 'CompileFarm.conf', 'exec'))
eval(compile(open('Slave.conf').read(), 'Slave.conf', 'exec'))

### Globals ###

return_success = 0x01
return_failure = 0x00

is_compiling = False
is_compiling_lock = threading.Lock()
read_lock = threading.Lock()

### Helper Functions ###

def Log(message, file=0) :
	if not file :
		print message
		return
	print message
	fp = open(file, "w")
	fp.write(message)
	fp.close()

def LogHtmlCommand(command, file=0) :
	if not file :
		Log(command)
		return
    
	# From mtail
	ansi = {}
	ansi["black"]         = chr(27)+"[0;30m"
	ansi["red"]           = chr(27)+"[0;31m"
	ansi["green"]         = chr(27)+"[0;32m"
	ansi["yellow"]        = chr(27)+"[0;33m"
	ansi["blue"]          = chr(27)+"[0;34m"
	ansi["magenta"]       = chr(27)+"[0;35m"
	ansi["cyan"]          = chr(27)+"[0;36m"
	ansi["white"]         = chr(27)+"[0;37m"
	ansi["brightblack"]   = chr(27)+"[1;30m"
	ansi["brightred"]     = chr(27)+"[1;31m"
	ansi["brightgreen"]   = chr(27)+"[1;32m"
	ansi["brightyellow"]  = chr(27)+"[1;33m"
	ansi["brightblue"]    = chr(27)+"[1;34m"
	ansi["brightmagenta"] = chr(27)+"[1;35m"
	ansi["brightcyan"]    = chr(27)+"[1;36m"
	ansi["brightwhite"]   = chr(27)+"[1;37m"
	ansi["reset"]         = chr(27)+"[0m"

	html = {}
	html["black"]         = "<font COLOR=#000000>"
	html["red"]           = "<font COLOR=#ff0000>"
	html["green"]         = "<font COLOR=#00ff00>"
	html["yellow"]        = "<font COLOR=#ffff00>"
	html["blue"]          = "<font COLOR=#0000ff>"
	html["magenta"]       = "<font COLOR=#ff00ff"
	html["cyan"]          = "<font COLOR=#408080>"
	html["white"]         = "<font COLOR=#eeeeee>"
	html["brightblack"]   = "<font COLOR=#808080>"
	html["brightred"]     = "<font COLOR=#e41b17>"
	html["brightgreen"]   = "<font COLOR=#22ff22>"
	html["brightyellow"]  = "<font COLOR=#ffe87c>"
	html["brightblue"]    = "<font COLOR=#00ffff>"
	html["brightmagenta"] = "<font COLOR=#ff0080"
	html["brightcyan"]    = "<font COLOR=#50ebec>"
	html["brightwhite"]   = "<font COLOR=#ffffff>"
	html["reset"]         = "</font>"
	

	fp = open(file, "w")
	fp.write('<html>\n')
	subprocess = popen2.Popen4(command)
	(i, oe) = (subprocess.tochild, subprocess.fromchild)
	for line in oe.readlines() :
		line = line.replace(ansi["reset"], html["reset"])
		for color in [ "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white" ] :
			line = line.replace(ansi[color], html[color])
			line = line.replace(ansi["bright"+color], html["bright"+color])
		fp.write(line.strip('\r\n')+'<br/>\n')
		Log(line.strip('\r\n')+'<br/>\n')
	fp.write('</html>\n')
	fp.close()

### Classes ###

class Job() :
	def __init__(self, program, version, arch) :
		self.program = program
		self.version = version
		self.arch = arch

	def Compile(self, remoteaddr) :
		# Use the latest revision available
		buf = os.popen('CheckoutRecipe %s' %self.program)
		if not buf :
			Log('> error checking out latest revision for %s.' %self.program)
			return False

		svndir = '%s/%s' %(compilefarmSubversionRevisionsDir, self.program)
		os.chdir(svndir)
		latest = os.popen('GuessLatest ' + self.version + '*').read().strip('\n\r')
		if not os.path.exists(svndir+'/'+latest) :
			Log('> error: %s/%s doesn\'t exist' %(appdir,version))
			return False

		self.version = latest

		# ChrootCompile
		os.chdir(slaveChrootCompileDir)
		logdir = 'logs/%s/%s' %(self.program, self.version)
		if not os.path.exists(logdir) :
			os.makedirs(logdir)
		logfile = logdir+'/ChrootCompile.log' 
		LogHtmlCommand('ChrootCompile --no-debug --verbose --no-sign --use-tmpfs --revisions-tree %s/%s' %(svndir, self.version), logfile)

		# Upload results
		filename = self.program + '--' + self.version + '--' + self.arch + '.tar.bz2'
		tarball = '%s/Clean/%s/%s' %(slaveChrootCompileDir, self.arch, filename)
		if os.path.exists(tarball) :
			Log('> copying tarball to master node')
			self.Upload('put_tarball', tarball, remoteaddr)
			Log('> done.')
		else :
			Log('> copying compilation error log to master node')
			self.Upload('put_log', logfile, remoteaddr)
			Log('> done.')
		return True

	def Upload(self, command, filename, remoteaddr) :
		read_lock.acquire()
		remoteaddr.send('%s %s %s %s %s' %(command, self.program, self.version, self.arch, os.path.basename(filename)))
		length = os.stat(filename).st_size
		remoteaddr.send(struct.pack('!L', length))
		buf = open(filename).read()
		totalsent = 0
		while totalsent < length :
			sent = remoteaddr.send(buf[totalsent:])
			if sent == 0 :
				Log('> Socket broken while uploading %s to %s' %(filename, remoteaddr))
				break
			totalsent += sent

		# wait for ack
		r = remoteaddr.recv(1)
		r = int(struct.unpack('!B', r)[0])
		read_lock.release()
		return r

	def UpdateLocalCopy(self, app) :
		Log('> svn up %s' %app)
		ret = os.popen('CheckoutRecipe %s' %app)
		while True: 
			line = ret.readline().strip('\n\r')
			if not line :
				break
			Log(line)

		ret.close()
		if not os.path.exists(svn_dir+'/'+app) :
			Log('> error: could not fetch recipe %s from the svn server.' %app)
			return 'Error fetching recipe from the svn server.'
		return 'Ok'


class PassiveConnectionHandler(threading.Thread) :
	def __init__(self, conn, address) :
		self.conn = conn
		self.remoteaddr = address
		threading.Thread.__init__(self)

	def run(self) :
		global is_compiling_lock
		global read_lock
		
		while True :
			try :
				read_lock.acquire()
				self.conn.settimeout(3)
				buf = self.conn.recv(4096)
				self.conn.settimeout(None)
				read_lock.release()
			except :
				self.conn.settimeout(None)
				read_lock.release()
				time.sleep(1)
				continue
			if not buf :
				break
		
			Log(self.remoteaddr + '> ' + buf.strip('\n\r'))
			bufcmd = buf.strip('\n\r').split()
			if not bufcmd :
				continue

			cmd = bufcmd[0]
			args = len(bufcmd)-1

			if not cmd or len(cmd) == 0 :
				continue
			
			elif cmd == 'announce' :
				if args != 3 :
					read_lock.acquire()
					self.conn.send(struct.pack('!B', return_failure))
					read_lock.release()
					continue

				is_compiling_lock.acquire()
				if not is_compiling :
					is_compiling = True
					is_compiling_lock.release()
					read_lock.acquire()
					self.conn.send(struct.pack('!B', return_success))
					read_lock.release()
					Job(bufcmd[1], bufcmd[2], bufcmd[3]).Compile(self.conn)
				else :
					is_compiling_lock.release()
					read_lock.acquire()
					self.conn.send(struct.pack('!B', return_failure))
					read_lock.release()
					continue

			elif cmd == 'abort' :
				if args != 3 :
					read_lock.acquire()
					self.conn.send(struct.pack('!B', return_failure))
					read_lock.release()
					continue
				os.system('killall -9 ChrootCompile')
				is_compiling_lock.acquire()
				is_compiling = False
				is_compiling_lock.release()
				read_lock.acquire()
				self.conn.send(struct.pack('!B', return_success))
				read_lock.release()

			else :
				Log('could not understand command %s' %cmd)
				read_lock.acquire()
				self.conn.send(struct.pack('!B', return_failure))
				read_lock.release()

		self.conn.close()


### Operation ###

server = socket(AF_INET, SOCK_STREAM)
server.connect((compilefarmListeningInterface, compilefarmListeningPort))

server.send('login')
buf = server.recv(4096)
if buf != 'Ok' :
	print 'Login error: %s' %buf
	sys.exit(1)

PassiveConnectionHandler(server, compilefarmListeningInterface).start()

# Request for jobs eternally
while True :
	time.sleep(3)

	is_compiling_lock.acquire()
	if is_compiling :
		is_compiling_lock.release()
		continue

	read_lock.acquire()
	server.send('get_job')
	buf = server.recv(4096)
	read_lock.release()

	if not buf or buf == 'No unassigned jobs found.' :
		is_compiling_lock.release()
		continue

	bufcmd = buf.strip('\n\r').split()
	if not bufcmd :
		is_compiling_lock.release()
		continue

	is_compiling = True
	is_compiling_lock.release()

	Job(bufcmd[0], bufcmd[1], bufcmd[2]).Compile(server)
	
	is_compiling_lock.acquire()
	is_compiling = False
	is_compiling_lock.release()

