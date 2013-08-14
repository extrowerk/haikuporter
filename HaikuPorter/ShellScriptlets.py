# -*- coding: utf-8 -*-
#
# Copyright 2013 Oliver Tappe
# Distributed under the terms of the MIT License.

# -- Modules ------------------------------------------------------------------

from string import Template

# -----------------------------------------------------------------------------

# A list of all the commands/packages that are prerequired in the chroot, such
# that these scriptlets have all the commands available that they are using.
scriptletPrerequirements = r'''
	coreutils
	cmd:bash
	cmd:grep
	cmd:${targetMachinePrefix}objcopy
	cmd:${targetMachinePrefix}readelf
	cmd:sed
	cmd:${targetMachinePrefix}strip
'''

def getScriptletPrerequirements(targetMachineTripleAsName = None):
	"""Returns the list of prerequirements for executing scriptlets.
	   If targetMachineTriple is given, the prerequirements will be specialized
	   for cross-building for the given target machine."""

	targetMachinePrefix \
		= (targetMachineTripleAsName + '_') if targetMachineTripleAsName else ''

	prerequirements = Template(scriptletPrerequirements).substitute({
		'targetMachinePrefix': targetMachinePrefix,
	}).splitlines()

	return prerequirements

def getShellVariableSetters(shellVariables):
	"""Converts a dict {variableName -> value} to a string with shell code to
	   set the variables to the respective value."""
	if not shellVariables:
		return ''

	result = '\n'.join("%s='%s'" % (k, v)
		for k, v in shellVariables.iteritems()) + '\n'

	# Add a variable "revisionVariables" that contains the name of all
	# variables that need to be reevaluated after the revision is known.
	revisionVariables = []
	for name, value in shellVariables.iteritems():
		if '$REVISION' in value:
			revisionVariables.append(name)
	if revisionVariables:
		result += 'revisionVariables="' + ' '.join(revisionVariables) + '"\n'

	return result


# -----------------------------------------------------------------------------

# Shell scriptlet that is used to trigger one of the actions defined in a build
commonRecipeScriptHead = r'''

# stop on every error
set -e

# common utility functions

getPackagePrefix()
{
	# Usage: getPackagePrefix <packageSuffix>
	local packageSuffix="$1"

	local packageLinksDir="$(dirname $installDestDir$portPackageLinksDir)"
	local packageName="${portName}_$packageSuffix"
	local linksDir="$packageLinksDir/$packageName-$portFullVersion"
	local packagePrefix="$linksDir/.self"

	if [ ! -e "$packagePrefix" ]; then
		echo >&2 "packagePrefix: error: \"$packageSuffix\" doesn't seem to be"
		echo >&2 "a valid package suffix."
		exit 1
	fi

	echo $packagePrefix
}

defineDebugInfoPackage()
{
	# Usage: defineDebugInfoPackage [ --directory <toDirectory> ]
	#	<basePackageName> <path> ...
	if [ $# -lt 2 -o "$1" = "--directory" -a $# -lt 4 ]; then
		echo >&2 "Usage: defineDebugInfoPackage [ --directory <toDirectory> ]"
			"<packageSuffix> <path> ..."
		exit 1
	fi

	local destDir=$debugInfoDir
	local debugInfoSuffix="($portRevisionedName)"
	if [ "$1" = "--directory" ]; then
		destDir="$2"
		shift 2
	fi

	local basePackageName=$1
	shift 1

	local packageName=${basePackageName}_debuginfo
	local packageSuffix=$(echo $packageName | sed s,[^_]*_,,)

	local provides=PROVIDES_$packageSuffix
	local requires=REQUIRES_$packageSuffix
	printf -v $provides "%s" "${packageName} = $portVersion"
	printf -v $requires "%s" "${basePackageName} == $portVersion base"

	# Use two array variables for a path->debugInfo map. An associative array
	# would be nicer, but we can't declare that to be global before bash 4.2
	# (declare option -g).
	local paths=DEBUG_INFO_PATHS_$packageSuffix
	local debugInfos=DEBUG_INFO_DEBUG_INFOS_$packageSuffix

	DEBUG_INFO_PACKAGES="$DEBUG_INFO_PACKAGES $packageSuffix"

	while [ $# -ge 1 ]; do
		local path=$1
		shift

		local providesEntity="debuginfo:$(basename $path)($basePackageName)"
		printf -v $provides "%s\n%s" "${!provides}" \
			"\"$providesEntity\" = $portVersion"

		local debugInfo="$destDir/$(basename $path)$debugInfoSuffix.debuginfo"
		eval "local count=\${#$paths[*]}"
		eval "$paths[$count]=\"$path\""
		eval "$debugInfos[$count]=\"$debugInfo\""
	done
}
'''


# -----------------------------------------------------------------------------

# Shell scriptlet that is used to execute a config file and output all the
# configuration values (in the form of environment variables) which have been
# set explicitly in the configuration file. The shell variables "fileToParse"
# and "supportedKeysPattern" must be set to the configuration file respectively
# a '|'-separated list of  supported configuration keys.
# Note: this script requires bash, it won't work with any other shells
configFileEvaluatorScript = commonRecipeScriptHead + r'''


updateRevisionVariables()
{
	local variable
	for variable in $revisionVariables; do
		eval "${variable}=\"${!variable}\""
	done
	unset revisionVariables
}

# source the configuration file
. $fileToParse >/dev/null

# select all environment vars we are interested in, which are all vars that
# match the given keys plus the ones that extend a given key by '_<something>'
supportedKeys=$(set | grep -E -o "^($supportedKeysPattern)(_[0-9a-zA-Z_]+)?=" \
	| cut -d= -f1)

# output the supported environment vars which have been set, quoting any
# newlines in their values
NL=$'\n'
for key in $supportedKeys
do
	if [[ -n ${!key+dummy} ]]
	then
		value=${!key}
		echo "$key=${value//$NL/\\n}"
	fi
done

for phase in ${recipePhases}; do
	if [ -n "$(type -t $phase)" ]; then
		echo "${phase}_DEFINED=1"
	fi
done
'''


# -----------------------------------------------------------------------------

# Shell scriptlet that is used to trigger one of the actions defined in a build
# recipe. The shell variables "fileToParse" and "recipeAction" must be set to
# the recipe file respectively the name of the action to be invoked.
recipeActionScript = commonRecipeScriptHead + r'''

# provide defaults for every action
BUILD()
{
	true
}

INSTALL()
{
	true
}

TEST()
{
	true
}

updateRevisionVariables()
{
	true
}

getTargetArchitectureCommand()
{
	# Usage: getTargetArchitectureCommand <command>
	if [ $# -lt 1 ]; then
		echo >&2 "Usage: getTargetArchitectureCommand <command>"
		exit 1
	fi

	local command=$1

	if [[ $isCrossRepository == true && $portName != *_cross_* ]]; then
		echo ${effectiveTargetMachineTriple}-$command
	else
		echo $command
	fi
}

# helper function to invoke a configure script with the correct directory
# arguments
runConfigure()
{
	# parse arguments
	varsToOmit=""

	while [ $# -ge 1 ]; do
		case $1 in
			--omit-dirs)
				shift 1
				if [ $# -lt 1 ]; then
					echo "runConfigure: \"--omit-dirs\" needs an argument!" >&2
				fi
				varsToOmit="$1"
				shift 1
				;;
			*)
				break
				;;
		esac
	done

	if [ $# -lt 1 ]; then
		echo "Usage: runConfigure [ --omit-dirs <dirsToOmit> ] <configure>" \
			"<argsToConfigure> ..." >&2
		echo "  <configure>" >&2
		echo "      The configure program to be called." >&2
		echo "  <dirsToOmit>" >&2
		echo "      Space-separated list of directory arguments not to be" >&2
		echo "      passed to configure, e.g. \"docDir manDir\" (single" >&2
		echo "      argument!)." >&2
		echo "  <argsToConfigure>" >&2
		echo "      The additional arguments passed to configure." >&2
		exit 1
	fi

	configure=$1
	shift 1

	# build the directory arguments string
	dirArgs=""
	for dir in $configureDirVariables; do
		if ! [[ "$varsToOmit" =~ (^|\ )${dir}($|\ ) ]]; then
			dirArgs="$dirArgs --${dir,,}=${!dir}"
		fi
	done

	$configure $dirArgs $@
}

fixDevelopLibDirReferences()
{
	# Usage: fixDevelopLibDirReferences <file> ...
	# Replaces instances of $libDir in the given files with $developLibDir.
	for file in $*; do
		sed -i "s,$libDir,$developLibDir,g" $file
	done
}

prepareInstalledDevelLib()
{
	if [ $# -lt 1 ]; then
		echo >&2 "Usage: prepareInstalledDevelLib <libBaseName>" \
			"[ <soPattern> [ <pattern> ] ]"
		echo "Moves libraries from \$prefix/lib to \$prefix/develop/lib and" >&2
		echo >&2 "creates symlinks as required."
		echo >&2 "  <libBaseName>"
		echo >&2 "      The base name of the library, e.g. \"libfoo\"."
		echo >&2 "  <soPattern>"
		echo >&2 "      The glob pattern to be used to enumerate the shared"
		echo >&2 '      library entries. Is appended to $libDir/${libBaseName}'
		echo >&2 '      to form the complete pattern. Defaults to ".so*".'
		echo >&2 "  <pattern>"
		echo >&2 "      The glob pattern to be used to enumerate all library"
		echo >&2 '      entries. Is appended to $libDir/${libBaseName} to form'
		echo >&2 '      the complete pattern. Defaults to ".*".'

		exit 1
	fi

	mkdir -p $installDestDir$developLibDir

	local libBaseName=$1
	local soPattern=$2
	local pattern=$3

	# find the shared library file and get its soname
	local sharedLib=""
	local sonameLib=""
	local soname=""
	local readelf=$(getTargetArchitectureCommand readelf)
	for lib in $installDestDir$libDir/${libBaseName}${soPattern:-.so*}; do
		if [ -f $lib -a ! -h $lib ]; then
			sharedLib=$lib
			sonameLine=$($readelf --dynamic $lib | grep SONAME)
			if [ -n "$sonameLine" ]; then
				soname=$(echo "$sonameLine" | sed 's,.*\[\(.*\)\].*,\1,')
				if [ "$soname" != "$sonameLine" ]; then
					sonameLib=$installDestDir$libDir/$soname
				else
					soname=""
				fi
			fi

			break;
		fi
	done

	# Move things/create symlinks: The shared library file and the symlink for
	# the soname remain where they are, but we create respective symlinks in the
	# development directory. Everything else is moved there.
	for lib in $installDestDir$libDir/${libBaseName}${pattern:-.*}; do
		if [ "$lib" = "$sharedLib" ]; then
			symlinkRelative -s $installDestDir$libDir/$(basename $lib) \
				$installDestDir$developLibDir/
		elif [ "$lib" = "$sonameLib" ]; then
			ln -s $(basename $sharedLib) $installDestDir$developLibDir/$soname
		else
			# patch .la files before moving
			if [[ "$lib" = *.la ]]; then
				fixDevelopLibDirReferences $lib
			fi

			mv $lib $installDestDir$developLibDir/
		fi
	done
}

prepareInstalledDevelLibs()
{
	while [ $# -ge 1 ]; do
		prepareInstalledDevelLib $1
		shift 1
	done
}

fixPkgconfig()
{
	sourcePkgconfigDir=$installDestDir$libDir/pkgconfig
	targetPkgconfigDir=$installDestDir$developLibDir/pkgconfig

	if [ ! -d $sourcePkgconfigDir ]; then
		return
	fi


	mkdir -p $targetPkgconfigDir

	for file in $sourcePkgconfigDir/*; do
		name=$(basename $file)
		sed -e 's,^libdir=\(.*\),libdir=${prefix}/develop/lib,' \
			-e 's,^includedir=\(.*\),includedir=${prefix}/develop/headers,' \
			$file > $targetPkgconfigDir/$name
	done

	rm -r $sourcePkgconfigDir
}

symlinkRelative()
{
	local flags
	while [ $# -ge 1 ] && [[ "$1" = -* ]]; do
		flags="$flags $1"
		shift 1
	done

	if [ $# -lt 2 ]; then
		echo "Usage: symlinkRelative <flags> <from> ... <to>" >&2
		exit 1
	fi

	declare -a fromPaths
	while [ $# -gt 1 ]; do
		fromPaths[${#fromPaths[@]}]="$1"
		shift 1
	done
	local toPath="$1"

	# make sure target path is absolute
	if [[ "$toPath" != /* ]]; then
		toPath="$(pwd)/$toPath"
	fi

	# get target path prefixes
	declare -a toPathPrefixes
	declare -a toPathUpPrefixes
	local path="$toPath"
	if [ -d "$path" ]; then
		path="$path/_"
	fi
	local upPrefix=
	while [ "$path" != / ]; do
		path="$(dirname "$path")"
		toPathPrefixes=("$path" "${toPathPrefixes[@]}")
		toPathUpPrefixes=("$upPrefix" "${toPathUpPrefixes[@]}")
		upPrefix=${upPrefix}../
	done

	# process the from paths
	declare -a processedFromPaths
	local fromPath
	for fromPath in "${fromPaths[@]}"; do
		# make sure target path is absolute
		if [[ "$fromPath" != /* ]]; then
			fromPath="$(pwd)/$fromPath"
		fi

		# get path prefixes
		declare -a fromPathPrefixes
		local path="$fromPath"
		while [ "$path" != / ]; do
			path="$(dirname "$path")"
			fromPathPrefixes=("$path" "${fromPathPrefixes[@]}")
		done

		# get the longest common prefix
		local commonPrefix
		local i
		for (( i=0 ; i<${#fromPathPrefixes[@]} ; i++ )) ; do
			if [ "${fromPathPrefixes[$i]}" != "${toPathPrefixes[$i]}" ]; then
				break
			fi
			commonPrefix="${fromPathPrefixes[$i]}"
			upPrefix="${toPathUpPrefixes[$i]}"
		done
		local prefixLength=${#commonPrefix}
		if [ $prefixLength -gt 1 ]; then
			prefixLength=$[$prefixLength + 1]
		fi
		local fromSuffix="${fromPath:$prefixLength}"
		local toSuffix="${toPath:$prefixLength}"

		# construct the relative path
		processedFromPaths[${#processedFromPaths[@]}]="$upPrefix$fromSuffix"
	done

	ln $flags "${processedFromPaths[@]}" "$toPath"
}

addAppDeskbarSymlink()
{
	# Usage: addAppDeskbarSymlink <appPath> [ <entryName> ]
	# Creates a Deskbar symlink for an application.
	# <appPath> is the absolute path to the application executable.
	# <entryName> is the name of the application as it shall appear in the
	# Deskbar. Can be omitted, in which case the name of the application
	# executable is used.
	if [ $# -lt 1 ]; then
		echo >&2 "Usage: addAppDeskbarSymlink <appPath> [ <entryName> ]"
		exit 1
	fi

	appPath="$1"
	shift 1
	if [ $# -lt 1 ]; then
		entryName="$(basename "$appPath")"
	else
		entryName=$1
	fi

	targetDir=$dataDir/deskbar/menu/Applications
	mkdir -p $targetDir
	symlinkRelative -s "$appPath" "$targetDir/$entryName"
}

addPreferencesDeskbarSymlink()
{
	# Usage: addPreferencesDeskbarSymlink <appPath> [ <entryName> ]
	# Creates a Deskbar symlink for a preferences application.
	# <appPath> is the absolute path to the application executable.
	# <entryName> is the name of the application as it shall appear in the
	# Deskbar. Can be omitted, in which case the name of the application
	# executable is used.
	if [ $# -lt 1 ]; then
		echo >&2 "Usage: addPreferencesDeskbarSymlink <appPath> [ <entryName> ]"
		exit 1
	fi

	appPath="$1"
	shift 1
	if [ $# -lt 1 ]; then
		entryName="$(basename "$appPath")"
	else
		entryName=$1
	fi

	targetDir=$dataDir/deskbar/menu/Preferences
	mkdir -p $targetDir
	symlinkRelative -s "$appPath" "$targetDir/$entryName"
}

packageEntries()
{
	# Usage: packageEntries <packageSuffix> <entry> ...
	# Moves the given entries to the packaging directory for the package
	# specified by package name suffix (e.g. "devel").
	# Entry paths can be absolute or relative to $prefix.

	if [ $# -lt 2 ]; then
		echo >&2 "Usage: packageEntries <packageSuffix> <entry> ..."
		exit 1
	fi

	local packageSuffix="$1"
	shift 1

	local packagePrefix=$(getPackagePrefix $packageSuffix)

	if [ ! -e "$packagePrefix" ]; then
		echo >&2 "packageEntries: error: \"$packageSuffix\" doesn't seem to be"
		echo >&2 "a valid package suffix."
		exit 1
	fi

	# move the entries
	for file in $*; do
		# If absolute, resolve to relative file name.
		if [[ "$file" = /* ]]; then
			if [[ "$file" =~ $installDestDir$prefix/(.*) ]]; then
				file=${BASH_REMATCH[1]}
			else
				echo >&2 "packageEntries: error: absolute entry \"$file\""
				echo >&2 "doesn't appear to be in \"$installDestDir$prefix\"."
			fi
		fi

		# make sure target containing directory exists and move there
		targetDir=$(dirname "$packagePrefix/$file")
		mkdir -p "$targetDir"
		mv "$installDestDir$prefix/$file" "$targetDir"
	done
}

extractDebugInfo()
{
	# Usage: extractDebugInfo <path> <debugInfoPath>
	if [ $# -ne 2 ]; then
		echo >&2 "Usage: extractDebugInfo <path> <debugInfoPath>"
		exit 1
	fi

	local path="$1"
	local debugInfoPath="$2"

	mkdir -p "$(dirname $debugInfoPath)"

	local objcopy=$(getTargetArchitectureCommand objcopy)
	local strip=$(getTargetArchitectureCommand strip)
	$objcopy --only-keep-debug "$path" "$debugInfoPath"
	$strip --strip-debug "$path"
	$objcopy --add-gnu-debuglink="$debugInfoPath" "$path"
}

packageDebugInfos()
{
	local packageSuffix
	for packageSuffix in $DEBUG_INFO_PACKAGES; do
		local paths=DEBUG_INFO_PATHS_$packageSuffix
		local debugInfos=DEBUG_INFO_DEBUG_INFOS_$packageSuffix

		eval "local count=\${#$paths[*]}"
		local i
		for i in $(seq 0 $[$count - 1]); do
			eval "local path=\${$paths[$i]}"
			eval "local debugInfo=\${$debugInfos[$i]}"
			extractDebugInfo "$path" "$debugInfo"
			packageEntries $packageSuffix "$debugInfo"

			# remove debug info directory, if empty, now
			local directory=$(dirname "$debugInfo")
			rmdir $directory 2> /dev/null || true
		done
	done
}

# source the configuration file
. $fileToParse >/dev/null

# invoke the requested action
if [[ $quiet ]]; then
	$recipeAction >/dev/null
else
	$recipeAction
fi

# post-INSTALL work
if [ $recipeAction = "INSTALL" ]; then
	packageDebugInfos
fi

'''


# -----------------------------------------------------------------------------

# Shell scriptlet that prepares a chroot environment for entering.
# Invoked with $packages filled with the list of packages that should
# be activated (via common/packages) and $recipeFilePath pointing to the
# recipe file.
# Additionally, $crossSysrootDir will be set to the cross-sysroot directory
# when the cross-build repository is active and $targetArchitecture will be
# filled with the target architecture.
setupChrootScript = r'''
# ignore sigint but stop on every error
trap '' SIGINT
set -e

mkdir -p \
	dev \
	boot/system/packages \
	boot/common/cache/tmp \
	boot/common/packages \
	boot/common/settings/etc \
	port \

ln -sfn /boot/system system
ln -sfn /boot/system/bin bin
ln -sfn /boot/system/package-links packages
ln -sfn /boot/common/cache/tmp tmp
ln -sfn /boot/common/settings/etc etc
ln -sfn /boot/common/var var

# remove any packages that may be lying around
rm -f boot/common/packages/*.hpkg
rm -f boot/system/packages/*.hpkg

# link all system packages
ln -s /boot/system/packages/*.hpkg boot/system/packages/

# link the list of required common packages
for pkg in $packages; do
	ln -sfn "$pkg" boot/common/packages/
done

# silently unmount if needed, just to be one the safe side
if [ -e dev/console ]; then
	unmount dev
fi
if [ -e boot/system/bin ]; then
	unmount boot/system
fi
if [ -e boot/common/bin ]; then
	unmount boot/common
fi
if [ -e port/work* ]; then
	unmount port
fi

# if it has been defined, mount the cross-build sysroot
if [[ -n $crossSysrootDir ]]; then
	if [ -e $crossSysrootDir/boot/system/develop ]; then
		unmount $crossSysrootDir/boot/system
	fi
	# symlink haiku_cross_devel package into place
	mkdir -p $crossSysrootDir/boot/system/packages
	crossDevelPath=/boot/system/develop/cross
	ln -sfn \
		$crossDevelPath/haiku_cross_devel_sysroot_$targetArchitecture.hpkg \
		$crossSysrootDir/boot/system/packages/haiku_cross_devel_sysroot.hpkg
	mount -t packagefs -p "type system" $crossSysrootDir/boot/system
fi

# mount dev, system-packagefs and common-packagefs
mount -t bindfs -p "source /dev" dev
mount -t packagefs -p "type system" boot/system
mount -t packagefs -p "type common" boot/common

# bind-mount the port directory to port/
mount -t bindfs -p "source $portDir" port

'''


# -----------------------------------------------------------------------------

# Shell scriptlet that cleans up a chroot environment after it has been exited.
# Invoked with $buildOk indicating if the build has worked and thus all paths
# required for building only should be wiped.
cleanupChrootScript = r'''

checkedUnmount()
{
	local mountPoint="$1"

	if ! [[ $mountPoint = /* ]]; then
		mountPoint=$PWD/$mountPoint
	fi

	# retry up to 5 times to unmount the given mountpoint
	local x=0
	while [ $x -lt 5 ]; do
		if unmount "$mountPoint"; then
			break
		fi
		let x+=1
		echo "unmounting $mountPoint failed - wait 1 second and retry ..."
		sleep 1
	done
}

# ignore sigint
trap '' SIGINT

# try to make sure we really are in a work directory
if ! echo $(basename $PWD) | grep -qE '^work-'; then
	echo "cleanupChroot invoked in $PWD, which doesn't seem to be a work dir!"
	exit 1
fi

# if it is defined, unmount the cross-build sysroot
if [[ -n $crossSysrootDir && -e $crossSysrootDir/boot/system/develop ]]; then
	checkedUnmount $crossSysrootDir/boot/system
fi

checkedUnmount dev
checkedUnmount boot/system
checkedUnmount boot/common
checkedUnmount port

# wipe files and directories if it is ok to do so
if [[ $buildOk ]]; then
	echo "cleaning chroot folder"
	rmdir dev port
	rm -rf \
		boot \
		build-packages \
		package-infos \
		packages \
		packaging \
		prereq-repository \
		repository
	rm -f \
		.PackageInfo \
		bin \
		etc \
		port.recipe \
		system \
		tmp \
		var
else
	echo "keeping chroot folder $PWD intact for inspection"
fi
'''
