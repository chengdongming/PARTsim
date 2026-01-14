/***************************************************************************
    begin                : Thu Apr 24 15:54:58 CEST 2003
    copyright            : (C) 2003 by Giuseppe Lipari
    email                : lipari@sssup.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
/*
 * $Id: resource.cpp,v 1.4 2005/04/28 01:34:48 cesare Exp $
 *
 * $Log: resource.cpp,v $
 * Revision 1.4  2005/04/28 01:34:48  cesare
 * Moved to sstream. Headers install. Code cleaning.
 *
 * Revision 1.3  2004/11/26 03:47:10  cesare
 * Finished merging the main trunk with Lipari's branch.
 *
 */
#include <rtsim/abstask.hpp>
#include <rtsim/resource/resource.hpp>

namespace RTSim {

    using namespace MetaSim;

    Resource::Resource(const string &n, int nr, int nr_available) :
        Entity(n),
        _total(nr),
        _available(nr_available),
        _available_initial(nr_available),
        _owner(0) {}

    Resource::Resource(const Resource &r) :
        Entity(r.getName() + "_copy"),
        _total(r.total()),
        _available(r._available),
        _available_initial(r._available_initial),
        _owner(0) {}

    void Resource::lock(AbsRTTask *t, int n) {
        _available -= n;
        _owner = t;
    }

    void Resource::unlock(int n) {
        _available += n;
        _owner = 0;
    }

    bool Resource::isLocked() const {
        return (_available == 0);
    }

    int Resource::available() const {
        return _available;
    }

    int Resource::total() const {
        return _total;
    }

    void Resource::newRun() {
        _available = _available_initial;
    }

    void Resource::endRun() {}

    AbsRTTask *Resource::getOwner() const {
        return _owner;
    }

} // namespace RTSim
