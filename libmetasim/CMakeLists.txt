set(LIBRARY_NAME                    metasim)
set(LIBRARY_TYPE                    SHARED)
set(LIBRARY_VERSION                 3.0)
set(LIBRARY_SOVERSION               3)
set(LIBRARY_INCLUDEDIR              include)
set(LIBRARY_INCLUDEDIR_PRIVATE      )
set(LIBRARY_INCLUDEDIR_INTERFACE    )
set(LIBRARY_PROPERTIES              )
set(LIBRARY_DEPENDENCIES            )

set(LIBRARY_SOURCE_FILES
    basestat.cpp
    debugstream.cpp
    entity.cpp
    event.cpp
    genericvar.cpp
    randomvar.cpp
    regvar.cpp
    simul.cpp
    strtoken.cpp
    tick.cpp
    trace.cpp
    )

# set(LIBRARY_PRECOMPILED_HEADER_FILES
#     <algorithm>
#     <array>
#     # <cassert>
#     # <cctype>
#     # <climits>
#     # <cmath>
#     # <cstddef>
#     # <cstdint>
#     # <cstdio>
#     # <cstdlib>
#     # <cstring>
#     # <ctime>
#     <deque>
#     <filesystem>
#     <fstream>
#     <functional>
#     <iomanip>
#     <iosfwd>
#     <iostream>
#     <iterator>
#     <limits>
#     <list>
#     <map>
#     <memory>
#     <numeric>
#     <ostream>
#     <set>
#     <sstream>
#     <stdexcept>
#     <streambuf>
#     <string>
#     <typeinfo>
#     <type_traits>
#     <utility>
#     <vector>

#     <metasim.hpp>
#     <metasim/baseexc.hpp>
#     <metasim/basetype.hpp>
#     <metasim/cloneable.hpp>
#     <metasim/debugstream.hpp>
#     <metasim/factory.hpp>
#     <metasim/genericvar.hpp>
#     <metasim/history.hpp>
#     <metasim/memory.hpp>
#     <metasim/particle.hpp>
#     <metasim/plist.hpp>
#     <metasim/randomvar.hpp>
#     <metasim/regvar.hpp>
#     <metasim/simul.hpp>
#     <metasim/strtoken.hpp>
#     <metasim/tick.hpp>
#     <metasim/trace.hpp>
#     <metasim/basestat.hpp>
#     <metasim/entity.hpp>
#     <metasim/event.hpp>
#     <metasim/gevent.hpp>
#     )

include(${PROJECT_SOURCE_DIR}/cmakeopts/library.cmake)
