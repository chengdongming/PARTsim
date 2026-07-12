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
#include <sstream>
#include <typeinfo>
#include <vector>

#include <metasim/entity.hpp>
#include <exception>
#include <metasim/simul.hpp>

// Used to demangle compiler class names
#include <metasim/demangle.hpp>

namespace MetaSim {

    using std::map;

    map<int, Entity *> Entity::_globMap;
    map<string, Entity *> Entity::_index;
    int Entity::_IDcount = 0;

    void Entity::_init() {
        if (_name == "") {
            std::stringstream ss;
            ss << _IDcount + 1;
            _name = string(demangle_compiler_name(typeid(*this).name())) + ss.str();
        }

        if (_index.find(_name) != _index.end())
            throw Exc("Creating an entity with the same name " + _name);

        _IDcount++;
        _ID = _IDcount;
        _globMap[_ID] = this;

        DBGENTER(_ENTITY_DBG_LEV);

        DBGPRINT("Entity ID: ", _ID);
        DBGPRINT("Entity type: ", demangle_compiler_name(typeid(*this).name()));
        DBGPRINT("Entity name: ", _name);

        _index[_name] = this;
    }

    Entity::Entity(const string &n) : _name(n) {
        _init();
    }

    Entity::~Entity() {
        _globMap.erase(_ID);
        _index.erase(_name);
    }

    Entity::Entity(const Entity &obj) : _name("") {
        std::stringstream ss;
        ss << obj._name << "_copy_" << _IDcount + 1;
        _name = ss.str();
        _init();
    }

    void Entity::callNewRun() {
        typedef map<int, Entity *>::iterator EI;

        EI p = _globMap.begin();
        std::vector<Entity *> initialized;
        try {
            while (p != _globMap.end()) {
                DBGENTER(_ENTITY_DBG_LEV);
                DBGPRINT("Calling the newRun() of ", p->second->getID());

                p->second->newRun();
                initialized.push_back(p->second);
                p++;
            }
        } catch (...) {
            const std::exception_ptr initialization_error =
                std::current_exception();
            // Only entities whose newRun() completed own run-local state.
            // Roll them back in reverse initialization order and preserve the
            // original initialization exception over any rollback failure.
            for (auto it = initialized.rbegin(); it != initialized.rend();
                 ++it) {
                try {
                    (*it)->endRun();
                } catch (...) {
                }
            }
            std::rethrow_exception(initialization_error);
        }
    }

    void Entity::callEndRun() {
        typedef map<int, Entity *>::iterator EI;

        std::exception_ptr first_error;
        EI p = _globMap.begin();
        while (p != _globMap.end()) {
            try {
                p->second->endRun();
            } catch (...) {
                if (!first_error)
                    first_error = std::current_exception();
            }
            p++;
        }
        if (first_error)
            std::rethrow_exception(first_error);
    }

    Entity *Entity::_find(string n) {
        Entity *res = 0;

        typedef map<string, Entity *>::iterator NI;

        NI i = _index.find(n);
        if (i != _index.end())
            res = (*i).second;
        return res;
    }

    std::ostream &operator<<(std::ostream &out, Entity &e) {
        return out << e.toString();
    }

} // end namespace MetaSim
