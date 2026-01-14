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
#ifndef __PIRESMAN_HPP__
#define __PIRESMAN_HPP__

#include <map>

#include <metasim/plist.hpp>

#include <rtsim/resource/resmanager.hpp>
#include <rtsim/scheduler/scheduler.hpp>

#define _PIRESMAN_DBG_LEV "piresman"

namespace RTSim {

    using namespace MetaSim;

    /// @ingroup resman
    ///
    /// Implements the Priority Inheritance Protocol. When a task blocks on a
    /// resource, it queries its scheduler for its priority and bumps the
    /// priority of the running task accordingly.
    class PIRManager : public ResManager {
    public:
        PIRManager(const std::string &n = "");

        // TODO: clear the maps!
        void newRun() override;
        void endRun() override;

    protected:
        bool request(AbsRTTask *t, Resource *r, int n = 1) override;
        void release(AbsRTTask *t, Resource *r, int n = 1) override;

    private:
        /// Correspondence between each task and its (original) priority
        using priority_map_t = std::map<Resource *, int>;

        /// Blocked tasks are ordered by priority, just like in the original
        /// scheduler.
        using prio_queue_t =
            priority_list<TaskModel *, TaskModel::TaskModelCmp>;

        /// Stores the old task priorities
        std::map<AbsRTTask *, priority_map_t> _oldPriorities;

        /// Stores the blocked tasks for each resource, ordered by priority
        std::map<Resource *, prio_queue_t> _blocked;

        /// Stores the list of tasks using the resource, ordered by priority
        std::map<Resource *, prio_queue_t> _running;
    };
} // namespace RTSim

#endif
