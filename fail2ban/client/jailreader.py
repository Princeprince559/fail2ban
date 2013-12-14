# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: t -*-
# vi: set ft=python sts=4 ts=4 sw=4 noet :

# This file is part of Fail2Ban.
#
# Fail2Ban is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Fail2Ban is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Fail2Ban; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

# Author: Cyril Jaquier
# 

__author__ = "Cyril Jaquier"
__copyright__ = "Copyright (c) 2004 Cyril Jaquier"
__license__ = "GPL"

import logging, re, glob, os.path

from configreader import ConfigReader
from filterreader import FilterReader
from actionreader import ActionReader

# Gets the instance of the logger.
logSys = logging.getLogger(__name__)

class JailReader(ConfigReader):
	
	optionCRE = re.compile("^((?:\w|-|_|\.)+)(?:\[(.*)\])?$")
	optionExtractRE = re.compile(
		r'([\w\-_\.]+)=(?:"([^"]*)"|\'([^\']*)\'|([^,]*))(?:,|$)')
	
	def __init__(self, name, force_enable=False, **kwargs):
		ConfigReader.__init__(self, **kwargs)
		self.__name = name
		self.__filter = None
		self.__force_enable = force_enable
		self.__actions = list()
	
	def setName(self, value):
		self.__name = value
	
	def getName(self):
		return self.__name
	
	def read(self):
		out = ConfigReader.read(self, "jail")
		# Before returning -- verify that requested section
		# exists at all
		if not (self.__name in self.sections()):
			raise ValueError("Jail %r was not found among available"
							 % self.__name)
		return out
	
	def isEnabled(self):
		return self.__force_enable or self.__opts["enabled"]

	@staticmethod
	def _glob(path):
		"""Given a path for glob return list of files to be passed to server.

		Dangling symlinks are warned about and not returned
		"""
		pathList = []
		for p in glob.glob(path):
			if not os.path.exists(p):
				logSys.warning("File %s doesn't even exist, thus cannot be monitored" % p)
			elif not os.path.lexists(p):
				logSys.warning("File %s is a dangling link, thus cannot be monitored" % p)
			else:
				pathList.append(p)
		return pathList

	def getOptions(self):
		opts = [["bool", "enabled", "false"],
				["string", "logpath", "/var/log/messages"],
				["string", "logencoding", "auto"],
				["string", "backend", "auto"],
				["int", "maxretry", 3],
				["int", "findtime", 600],
				["int", "bantime", 600],
				["string", "usedns", "warn"],
				["string", "failregex", None],
				["string", "ignoreregex", None],
				["string", "ignoreip", None],
				["string", "filter", ""],
				["string", "action", ""]]
		self.__opts = ConfigReader.getOptions(self, self.__name, opts)
		
		if self.isEnabled():
			# Read filter
			filterName, filterOpt = JailReader.extractOptions(
				self.__opts["filter"])
			self.__filter = FilterReader(
				filterName, self.__name, filterOpt, basedir=self.getBaseDir())
			ret = self.__filter.read()
			if ret:
				self.__filter.getOptions(self.__opts)
			else:
				logSys.error("Unable to read the filter")
				return False
			
			# Read action
			for act in self.__opts["action"].split('\n'):
				try:
					if not act:			  # skip empty actions
						continue
					actName, actOpt = JailReader.extractOptions(act)
					action = ActionReader(
						actName, self.__name, actOpt, basedir=self.getBaseDir())
					ret = action.read()
					if ret:
						action.getOptions(self.__opts)
						self.__actions.append(action)
					else:
						raise AttributeError("Unable to read action")
				except Exception, e:
					logSys.error("Error in action definition " + act)
					logSys.debug("Caught exception: %s" % (e,))
					return False
			if not len(self.__actions):
				logSys.warning("No actions were defined for %s" % self.__name)
		return True
	
	def convert(self, allow_no_files=False):
		"""Convert read before __opts to the commands stream

		Parameters
		----------
		allow_missing : bool
		  Either to allow log files to be missing entirely.  Primarily is
		  used for testing
		 """

		stream = []
		for opt in self.__opts:
			if opt == "logpath" and	\
					self.__opts.get('backend', None) != "systemd":
				found_files = 0
				for path in self.__opts[opt].split("\n"):
					pathList = JailReader._glob(path)
					if len(pathList) == 0:
						logSys.error("No file(s) found for glob %s" % path)
					for p in pathList:
						found_files += 1
						stream.append(["set", self.__name, "addlogpath", p])
				if not (found_files or allow_no_files):
					raise ValueError(
						"Have not found any log file for %s jail" % self.__name)
			elif opt == "logencoding":
				stream.append(["set", self.__name, "logencoding", self.__opts[opt]])
			elif opt == "backend":
				backend = self.__opts[opt]
			elif opt == "maxretry":
				stream.append(["set", self.__name, "maxretry", self.__opts[opt]])
			elif opt == "ignoreip":
				for ip in self.__opts[opt].split():
					# Do not send a command if the rule is empty.
					if ip != '':
						stream.append(["set", self.__name, "addignoreip", ip])
			elif opt == "findtime":
				stream.append(["set", self.__name, "findtime", self.__opts[opt]])
			elif opt == "bantime":
				stream.append(["set", self.__name, "bantime", self.__opts[opt]])
			elif opt == "usedns":
				stream.append(["set", self.__name, "usedns", self.__opts[opt]])
			elif opt == "failregex":
				stream.append(["set", self.__name, "addfailregex", self.__opts[opt]])
			elif opt == "ignoreregex":
				for regex in self.__opts[opt].split('\n'):
					# Do not send a command if the rule is empty.
					if regex != '':
						stream.append(["set", self.__name, "addignoreregex", regex])
		stream.extend(self.__filter.convert())
		for action in self.__actions:
			stream.extend(action.convert())
		stream.insert(0, ["add", self.__name, backend])
		return stream
	
	#@staticmethod
	def extractOptions(option):
		option_name, optstr = JailReader.optionCRE.match(option).groups()
		option_opts = dict()
		if optstr:
			for optmatch in JailReader.optionExtractRE.finditer(optstr):
				opt = optmatch.group(1)
				value = [
					val for val in optmatch.group(2,3,4) if val is not None][0]
				option_opts[opt.strip()] = value.strip()
		return option_name, option_opts
	extractOptions = staticmethod(extractOptions)