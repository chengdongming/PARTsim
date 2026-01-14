# PARTSim

A Power-Aware Real-Time System Simulator.

The name derives from the fact that this is a fork of RTSim, originally designed and developed by Giuseppe Lipari.

## Quick Guide

> For an ever quicker start guide, refer to [this one][quickstart].

The simulator can be easily built using [CMake] after downloading its mandatory dependencies. It is preferrable to clone the project with `git`, since `git` is also used to manage its mandatory dependencies. Python 3 is also necessary if you want to use the supplied builder script, but you can build it without using only standard CMake commands if you want.

If you only care about building the simulator, you can create a *shallow clone* of the project and build it in a few easy steps:

<!-- git clone https://gitlab.retis.santannapisa.it/parts/PARTSim.git --depth 1 PARTSim -->

```bash
git clone https://github.com/gabrieleara/PARTSim.git --depth 1 PARTSim
cd PARTSim
git submodule update --recursive --init
./tools/builder.py -cJ build
```

> NOTE: you can supply any of the mirrors for the project instead of the link above.

In the project directory there is now a `build` directory which contains the following files (among others):

| Files                                                                                                                | Description          |
| :------------------------------------------------------------------------------------------------------------------- | :------------------- |
| `build/libmetasim/libmetasim.so` <br/> `build/libmetasim/libmetasim.so.3` <br/> `build/libmetasim/libmetasim.so.3.0` | The MetaSim library  |
| `build/librtsim/librtsim.so` <br/> `build/librtsim/librtsim.so.3` <br/> `build/librtsim/librtsim.so.3.0`             | The RTSim library    |
| `build/rtsim/rtsim`                                                                                                  | The RTSim executable |

<!----------------------------------------------------------------------------->

## Full Guide

### Dependencies

So far, the project has the following dependencies:

| Dependency  | Type   | Mandatory | Description                                                                           |
| ----------- | ------ | --------- | ------------------------------------------------------------------------------------- |
| [CMakeOpts] | Build  | Yes       | A set of options for CMake, used to streamline the building process                   |
| [CmdArg]    | Source | Yes       | A simple library for handling command-line options                                    |
| [Eigen3]    | Source | No        | A powerful mathematical library used by the experimental thermal model implementation |

*Build* dependencies are only needed by the build system, while *Source* dependencies provide libraries needed by the simulator.

*Mandatory* dependencies are automatically downloaded by issuing
```
git submodule update --recursive --init
```
while optional ones must be obtained by different means.

### Managing Optional Dependencies

The experimental thermal model implementation (excluded by default in the build process) depends on [Eigen3]. Compiling it on your machine requires both library and headers installed.

On Debian-based distributions, you can install the Eigen3 library and headers by running:
```bash
sudo apt update
sudo apt install libeigen3-dev -y
```

### Building

Building PARTSim requires [CMake], optionally using [Ninja] as a CMake generator (default), making compilation time pretty fast. Ninja, however, is not required, with GNU Make set as fall back generator if Ninja is missing.

You don't have to issue CMake commands to build PARTSim. A (Python-based) builder script provided by [CMakeOpts] is used to simplify the build process. The script invokes CMake commands with suitable options, handling all sorts of situations for you.

To build the simulator using the builder script with multi-threaded compilation enabled, run:
```bash
./tools/builder.py -cJ build
```

The builder script accepts many options and commands. Use the `--help` option (or run the script with no option/command) to see a list of accepted arguments.

#### Special Note on Using the EnergyMRTKernel Class

The `EnergyMRTKernel` is not supported anymore and maintained in the code base for now just for practicality. Users should not look at it or even get remotely close to it.

#### Enabling the Thermal Model

If you want to build the experimental thermal model with the PARTSim library (provided you have installed the Eigen3 dependency [see above](#managing-optional-dependencies)), you must pass the option `-D RTLIB_THERMAL=ON` to the builder script.

Example:
```bash
./tools/builder.py -D RTLIB_THERMAL=ON build # [ other options... ]
```

<!----------------------------------- Links ----------------------------------->

[cmake]: https://cmake.org/cmake/help/latest/
[ninja]: https://ninja-build.org/
[eigen3]: https://eigen.tuxfamily.org/index.php?title=Main_Page
[cmakeopts]: https://github.com/gabrieleara/cmakeopts
[cmdarg]: https://github.com/gabrieleara/cmdarg
[quickstart]: QUICKSTART.md
