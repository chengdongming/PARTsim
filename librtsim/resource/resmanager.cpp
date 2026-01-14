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
 * $Id: resmanager.cpp,v 1.4 2005/04/28 01:34:48 cesare Exp $
 *
 * $Log: resmanager.cpp,v $
 * Revision 1.4  2005/04/28 01:34:48  cesare
 * Moved to sstream. Headers install. Code cleaning.
 *
 * Revision 1.3  2004/11/26 03:47:10  cesare
 * Finished merging the main trunk with Lipari's branch.
 *
 */
#include <metasim/entity.hpp>

#include <rtsim/abskernel.hpp>
#include <rtsim/abstask.hpp>
#include <rtsim/resource/resmanager.hpp>
#include <rtsim/resource/resource.hpp>

namespace RTSim {

    using namespace MetaSim;

    ResManager::ResManager(const string &n) : Entity(n) {}

    void ResManager::addResource(const string &name, int nr, int nr_initial) {
        _resources.insert(
            {name, std::make_shared<Resource>(name, nr, nr_initial)});
    }

    bool ResManager::hasResource(const string &name) const {
        return _resources.find(name) != _resources.end();
    }

    bool ResManager::request(AbsRTTask *t, const std::string &name, int nr) {
        DBGENTER(_RESMAN_DBG_LEV);
        auto resource = _resources.find(name);
        if (resource == _resources.end())
            throw BaseExc("Cannot find requested resouce " + name);

        return request(t, resource->second.get(), nr);
    }

    void ResManager::release(AbsRTTask *t, const std::string &name, int nr) {
        DBGENTER(_RESMAN_DBG_LEV);
        auto resource = _resources.find(name);
        if (resource == _resources.end())
            throw BaseExc("Cannot find requested resouce " + name);

        release(t, resource->second.get(), nr);
    }

} // namespace RTSim
