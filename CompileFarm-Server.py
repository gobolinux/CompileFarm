#!/usr/bin/env python

#
# GoboLinux CompileFarm Gateway
# (C) 2008 Lucas C. Villa Real. Released under the GNU GPL version 2.
#

import os, re, sys, threading, mutex, struct
from socket import *

### Slave Indices ###

SLAVE_ADDR = 0
SLAVE_ARCH = 1
SLAVE_SOCK = 2

eval(compile(open('CompileFarm.conf').read(), 'CompileFarm.conf', 'exec'))

### Globals ###

return_success = 0x01
return_failure = 0x00

compile_farm_version = '0.0.1'
job_lock = threading.Lock()

### Helper Functions ###

def Send(conn, message) :
	Log("<< " + message)
	conn.send(message)

def Log(message) :
	print message

def MakeDir(dir) :
	if not os.path.exists(dir) :
		try :
			os.makedirs(dir)
		except OSError, what :
			(errno, strerror) = what
			Log('> Error creating directory %s: %s' %(dir, strerror))
			return False
	return True

def ListDir(dir) :
	contents = os.listdir(dir)
	if '.svn' in contents :
		contents.remove('.svn')
	return contents

### Classes ###

class Slave() :
	def __init__(self, remoteaddr) :
		self.slave = None
		for slave in compilefarmSlaves :
			if slave[SLAVE_ADDR] == remoteaddr :
				self.slave = slave
				break

	def GetSocket(self) :
		return self.slave[SLAVE_SOCK]

	def SetSocket(self, socket) :
		self.slave[SLAVE_SOCK] = socket

	def GetArchs(self) :
		return self.slave[SLAVE_ARCH]

	def IsConnected(self) :
		return self.slave[SLAVE_SOCK]

	def IsSlave(self) :
		return self.slave

	def RemoteAddress(self) :
		return self.slave[SLAVE_ADDR]

	def SendMessage(self, message) :
		s = self.GetSocket()
		if s :
			Log('sending \'%s\' to %s' %(message, self.RemoteAddress()))
			s.send(message)
			r = s.recv(1)
			r = int(struct.unpack('!B', r)[0])
			return r
		return struct.pack('!B', return_failure)

	def ReceiveMessage(self) :
		# This is where we'll read large buffers. The first 4 bytes describe how big the data is.
		length = self.GetSocket().recv(4)
		length = int(struct.unpack('!L', length)[0])
		buf = ''
		while len(buf) < length :
			data = self.GetSocket().recv(4096)
			if not data or data == '':
				Log('Socket broken while receiving data from %s' %self.RemoteAddress())
				break
			buf += data
		return buf


class Job() :
	def __init__(self, program, version, arch) :
		self.program = program
		self.version = version
		self.arch = arch
		self.jobdir = None
		self.archdir = None
		self.hasprogram = False
		self.hasversion = False
		if program :
			os.system('CheckoutRecipe --batch %s' %program)
		if program and os.path.exists(compilefarmSubversionRevisionsDir + '/' + program) :
			self.jobdir = self.GetLatestRevision(compilefarmSubversionRevisionsDir + '/' + program, version)
			self.hasprogram = True
		if self.jobdir :
			self.archdir = self.jobdir + '/' + arch
			self.hasversion = True
			MakeDir(self.archdir)

	def GetLatestRevision(self, dir, version) :
		format = '((' + version + '(-r|))+.*)'
		format_c = re.compile(format)
		
		os.chdir(dir)
		latest = os.popen('GuessLatest ' + version + '*').read().strip('\n\r')
		latest = dir.replace(compilefarmSubversionRevisionsDir, compilefarmDir) + '/' + latest
		print 'latest is ' + latest
		return latest

	def GetArchs(self, program, version) :
		archs = []
		try :
			for arch in ListDir('%s/%s/%s' %(compilefarmDir, program, version)) :
				archs.append(arch)
		except OSError, errno :
			pass
		return archs

	def Create(self) :
		buf = os.popen('FindPackage --types=local_package,official_package %s %s' %(self.program, self.version))
		if buf and '.tar.bz2' in buf :
			return (False, 'A binary package is already available.')
		elif not self.hasprogram :
			return (False, 'Error: requested program doesn\'t exist in the recipe store.')
		elif not self.hasversion :
			return (False, 'Error: requested version doesn\'t exist in the recipe store.')
		elif not MakeDir(self.archdir) :
			return (False, 'Error: could not create a status directory for the requested program.')
		return (True, 'Job created.')

	def Abort(self) :
		if not self.Exists() :
			return 0
		owner = self.GetOwner()
		if owner :
			Slave(owner).SendMessage('abort %s %s %s' %(self.program, self.version, self.arch))
		os.system('rm -rf ' + self.archdir)
		return 0

	def Announce(self) :
		for s in compilefarmSlaves :
			slave = Slave(s[SLAVE_ADDR])
			r = slave.SendMessage('announce %s %s %s' %(self.program, self.version, self.arch))
			r = int(struct.unpack('!B', r)[0])
			if r == return_success :
				this.SetOwner(slave.RemoteAddress())
				break
	
	def Exists(self) :
		return os.path.exists(self.archdir)
	
	def GetOwner(self) :
		global job_lock
		job_lock.acquire()
		try :
			f = open('%s/Owner' %self.archdir, 'r')
			owner = f.read()
			f.close()
		except IOError :
			owner = None
		job_lock.release()
		return owner
	
	def SetOwner(self, owner) :
		MakeDir(self.archdir)
		global job_lock
		job_lock.acquire()
		f = open('%s/Owner' %self.archdir, 'w')
		f.write(owner)
		f.close()
		job_lock.release()

	def GetStatus(self) :
		if not self.Exists() :
			return '%s %s %s: No information available for the requested program.' %(self.program, self.version, self.arch)
		elif self.HasBinaryPackage() :
			return '%s %s %s: A binary package is now available.' %(self.program, self.version, self.arch)
		elif self.HasLog() :
			return self.GetLog()
		elif self.GetOwner() :
			return '%s %s %s: Compilation in progress.' %(self.program, self.version, self.arch)
		else :
			return '%s %s %s: Waiting for a slave.' %(self.program, self.version, self.arch)

	def HasBinaryPackage(self) :
		if not os.path.exists(self.archdir) :
			return False
		contents = ListDir(self.archdir)
		if '.tar.bz2' in contents :
			return True
		return False

	def HasLog(self) :
		if not os.path.exists('%s/ChrootCompile.log' %self.archdir) :
			return False
		return True

	def GetLog(self) :
		return open('%s/ChrootCompile.log' %self.archdir).read()
	
	def PutFile(self, owner, filename) :
		buf = Slave(owner).ReceiveMessage()
		if not buf :
			return struct.pack('!B', return_failure)
		f = open('%s/%s' %(self.archdir, filename), 'w')
		f.write(buf)
		f.close()
		return struct.pack('!B', return_success)

	def HasPartOf(self, dir) :
		for d in [ dir, dir+'/i686', dir+'/arm', dir+'/ppc', dir+'/sh4' ] :
			if os.path.exists(d+'/Recipe') :
				buf = open(d+'/Recipe').read()
				if 'part_of=' in buf :
					return True
		return False

	def GetUnassigned(self, owner) :
		assigned = None
		# first loop: look for user requested recipes
		for program in ListDir(compilefarmDir) :
			vlist = ListDir('%s/%s' %(compilefarmDir, program))
			# TODO: use GuessLatest. We don't need to list old versions
			for version in ListDir('%s/%s' %(compilefarmDir, program)) :
				for arch in self.GetArchs(program, version) :
					dir = '%s/%s/%s/%s' %(compilefarmDir, program, version, arch)
					if not arch in Slave(owner).GetArchs() :
						continue
					elif not os.path.exists('%s/Owner' %dir) :
						self.archdir = dir
						self.SetOwner(owner)
						return program + ' ' + version + ' ' + arch

		# second loop: take any recipe which doesn't have a corresponding binary package
		for program in ListDir(compilefarmSubversionRevisionsDir) :
			for version in ListDir('%s/%s' %(compilefarmSubversionRevisionsDir, program)) :
				# ignore entries covered by the first loop
				if len(self.GetArchs(program, version)) :
					continue

				# skip recipes containing 'part_of='
				if self.HasPartOf('%s/%s/%s' %(compilefarmSubversionRevisionsDir, program, version)) :
					continue

				# we found a candidate -- take the first available architecture
				arch = Slave(owner).GetArchs()[0]
				self.archdir = '%s/%s/%s/%s' %(compilefarmDir, program, version, arch)
				self.SetOwner(owner)
				return program + ' ' + version + ' ' + arch

		return 'No unassigned jobs found.'
		

class ConnectionHandler(threading.Thread) :
	def __init__(self, conn, remoteaddr) :
		self.conn = conn
		self.remoteaddr = remoteaddr
		self.slave = False
		threading.Thread.__init__(self)

	def run(self) :
		Log('Connection established with %s' %self.remoteaddr)
		while True :
			buf = conn.recv(4096)
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
		
			elif cmd == 'abort' :
				if args != 3 :
					Send(conn, 'Error: invalid syntax')
					continue
				Job(bufcmd[1], bufcmd[2], bufcmd[3]).Abort()
				Send(conn, 'Ok.')

			elif cmd == 'compile' :
				if args != 3 :
					Send(conn, 'Error: invalid syntax')
					continue
				job = Job(bufcmd[1], bufcmd[2], bufcmd[3])
				if job.GetOwner() :
					Send(conn, 'Compilation in progress.')
					continue
				(status, message) = job.Create()
				Send(conn, message)
				if status == True :
					job.Announce()
				else :
					print 'job.Create failed: %s' %message

			elif cmd == 'login' :
				slave = Slave(self.remoteaddr)
				if slave.IsSlave() :
					slave.SetSocket(conn)
					self.slave = True
					Send(conn, 'Ok')
				else :
					Send(conn, 'Command available for registered slaves only.')

			elif cmd == 'get_job' :
				if not self.slave :
					Send(conn, 'Command available for registered slaves only.')
					continue
				Send(conn, Job(None,None,None).GetUnassigned(self.remoteaddr))

			elif cmd == 'put_log' or cmd == 'put_tarball' :
				if args != 4 :
					print 'invalid syntax'
					Send(conn, 'Error: invalid syntax')
					continue
				if not self.slave :
					print 'not a slave'
					Send(conn, 'Command available for registered slaves only.')
					continue
				Send(conn, Job(bufcmd[1], bufcmd[2], bufcmd[3]).PutFile(self.remoteaddr, bufcmd[4]))

			elif cmd == 'help' :
				Send(conn, 'Available options for common users are:\n'
						  '* compile <app_name> <app_version> <arch>      Submit a job to the Compile Farm\n'
						  '* status [<app_name> <app_version> <arch>]     Get status about a given job or all submitted jobs\n'
						  '* abort   <app_name> <app_version> <arch>      Abort a compilation in progress for the given application\n'
						  '* version                                      Return this application\'s version\n'
						  '* quit                                         Quit this program\n\n'
						  'Available options for slaves are:\n'
						  '* login                                        Request authentication as a slave\n'
						  '* get_job                                      Request a job to compile\n'
						  '* put_log <app_name> <app_version> <arch>      Upload the compilation log\n'
						  '* put_tarball <app_name> <app_version> <arch>  Upload the tarball for a given application\n\n')

			elif cmd == 'status' :
				if args == 3 :
					Send(conn, Job(bufcmd[1], bufcmd[2], bufcmd[3]).GetStatus())
				elif args == 0 :
					Send(conn, Job(None, None, None).GetStatus())
				else :
					Send(conn, 'Error: invalid syntax')

			elif cmd == 'version' :
				Send(conn, compile_farm_version)

			elif cmd == 'quit' :
				break

			else :
				Send(conn, 'Error: invalid command \'%s\'' %cmd)
				break
		
		conn.close()
		Log('Closed connection with %s' %self.remoteaddr)
		
		if Slave(self.remoteaddr).IsSlave() :
			Slave(self.remoteaddr).SetSocket(None)


### Main() ###
if not os.path.exists(compilefarmDir) :
	print 'Creating directory "%s"...' %compilefarmDir
	try : 
		os.makedirs(compilefarmDir)
	except OSError, what :
		(errno, strerror) = what
		print strerror +', bailing out.'
		sys.exit(1)

server = socket(AF_INET, SOCK_STREAM)
server.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
server.bind((compilefarmListeningInterface, compilefarmListeningPort))
server.listen(5)

print 'Accepting connections.'
while True :
	conn, remoteaddr = server.accept()
	ConnectionHandler(conn, remoteaddr[0]).start()
