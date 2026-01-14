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
#ifndef __RESMANAGER_HPP__
#define __RESMANAGER_HPP__

#include <map>
#include <set>
#include <string>

#include <metasim/entity.hpp>
#include <rtsim/scheduler/scheduler.hpp>

#define _RESMAN_DBG_LEV "ResMan"

namespace RTSim {

    class AbsRTTask;
    class Resource;
    class AbsKernel;

    using namespace MetaSim;

    /// @ingroup resman
    ///
    /// Generic resource manager. A specific resource manager should be derived
    /// from this class by implementing its abstract methods.
    ///
    /// @see Resource
    class ResManager : public Entity {
        friend class RTKernel;

    public:
        /// Base constructor for a resource manager
        ResManager(const std::string &n = "");

        ~ResManager() override = default;

        /// Adds the resource to the set of resources manager by this object.
        /// Subsequent calls with the same resource name will be ignored (only
        /// the first one will be accepted).
        ///
        /// @param[in] name         The resource identifier
        ///
        /// @param[in] nr           The number of units (for supporting
        /// multi-unit resources), leave 1 for mutual exclusion.
        ///
        /// @param[in] nr_initial   The number of units available at the
        /// beginning of each simulation (at most nr, default=1).
        virtual void addResource(const std::string &name, int nr = 1,
                                 int nr_initial = 1);

        /// Returns true if the resource is present in this manager.
        ///
        /// NOTE: resources with the same name cannot be managed by multiple
        /// managers!
        virtual bool hasResource(const std::string &name) const;

        /// Requests access to a resource. Called by WaitInstr.
        ///
        /// Access is either immediately granted or the task is suspended.
        ///
        /// @returns true if access to the resource has been authorized. false
        /// if the task has been blocked.
        ///
        /// @param[inout] t
        ///
        /// @param[inout] kernel
        ///
        /// @param[inout] scheduler
        ///
        /// @param[in] name
        ///
        /// @param[in] nr number of units (by default is 1).
        bool request(AbsRTTask *t, const std::string &name, int nr = 1);

        /// Releases one or more units of a resource. Called by SignalInstr.
        ///
        /// After this call, one or more tasks may be reactivated if previously
        /// blocked on that resource.
        ///
        /// FIXME: check that the resource is locked? Or that you can request N
        /// instances of that resource?
        ///
        /// @param[inout] t
        ///
        /// @param[inout] kernel
        ///
        /// @param[inout] scheduler
        ///
        /// @param[in] name
        ///
        /// @param[in] nr number of units (by default is 1).
        void release(AbsRTTask *t, const std::string &name, int n = 1);

    protected:
        /// Set of resources managed by this manager, each identified by its
        /// name.
        std::map<std::string, std::shared_ptr<Resource>> _resources;

        virtual bool request(AbsRTTask *t, Resource *r, int n = 1) = 0;
        virtual void release(AbsRTTask *t, Resource *r, int n = 1) = 0;
    };
} // namespace RTSim

#endif
