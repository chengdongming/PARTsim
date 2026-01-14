// #pragma once

#ifndef __RTSIM_CPUISLAND_HPP__
#define __RTSIM_CPUISLAND_HPP__

#include <metasim/entity.hpp>
#include <rtsim/opp.hpp>
#include <rtsim/powermodel.hpp>
#include <rtsim/system_descriptor.hpp>

#include <cassert>
#include <set>

// FOR NOW, I WILL ASSUME THAT THE LIFETIME OF ISLANDS AND CPUS ARE NOT RELATED
// Nonetheless, associations between CPUs and CPUIslands are maintained
// consistent by their respective destructors.

// TODO: Constructors for these two classes are kind of arbitrary, the correct
// way to instantiate these should be to use some kind of factory that manages
// also their lifetime, but for now I'm using mostly-compatible constructors
// with previous implementations.

// TODO: Possibility to set a "fake" number of processors for the Island; the
// number of processors is used in the calculation of the power consumption of
// the individual CPUs, but as it is now it works correctly only if the Island
// has actually the correct number of DISTINCT CPUs (adding multiple times the
// same CPU will not work).

// TODO: Mechanism to indicate the CPU capability (based on the speed, valid
// only when using the Minimal CPU Model), used by (Energy)MRTKernel to select
// automatically the operating condition of each CPU. Also, the mechanism to
// select the desired OPP based on capability as well. In multi-core scenarios
// (with multiple cores in the same island), this should take into account the
// utilization of each CPU in the same island and not select an OPP which is
// lower than the necessary for another CPU.

// TODO: CPU capability

namespace RTSim {

    using namespace MetaSim;

    class CPU;
    class RTKernel;

    // =========================================================================
    // class CPUIsland
    // =========================================================================

    // TODO: count frequency switches and other stuff like
    // maximum registered power, power saving etc.

    class CPUIsland : public Entity {
    public:
        /// The CPUIsland type.
        ///
        /// This struct is actually an enum masked to have
        /// methods and can only be constructed with values
        /// specified in the _type enum.
        ///
        /// Virtually, it behaves like an "enum class" of
        /// C++ (so a qualifier must be used to access its
        /// values), but it also has methods, similarly to
        /// Java enum types.
        struct Type {
        public:
            enum T {
                GENERIC = 0,
                BIG,
                LITTLE,
                UNKNOWN,
            };

        private:
            /// Underlying value
            T t_;

        public:
            /// Constructible only  from the given values
            Type(T t = T::GENERIC) : t_(t) {}

            /// Automatic conversion to the underlying enum,
            /// to enable its usage in switch-case scenarios
            operator T() const {
                return t_;
            }

            /// Equality operator
            bool operator==(const Type &rhs) {
                return t_ == rhs.t_;
            }

            /// Inequality operator
            bool operator!=(const Type &rhs) {
                return t_ != rhs.t_;
            }

            /// Equality operator for T
            bool operator==(const T &rhs_t) {
                return t_ == rhs_t;
            }

            /// Inequality operator for T
            bool operator!=(const T &rhs_t) {
                return t_ != rhs_t;
            }

            // TODO: add other operators!

            // T operator()() const {
            //     return T();
            // }

        public:
            std::string toString() const {
                switch (t_) {
                case GENERIC:
                    return "Island_Generic";
                case BIG:
                    return "Island_big";
                case LITTLE:
                    return "Island_LITTLE";
                case UNKNOWN:
                default:
                    assert(false);
                    return "Island_Unknown";
                }
            }

        private:
            // prevent automatic conversion for any other built-in types such as
            // bool, int, etc
            template <typename T>
            operator T() const; // = delete;
        };

        // =================================================
        // =================================================

    private:
        static CPUModel *createMinimalPowerModel(const std::vector<OPP> &opps) {
            if (opps.size() < 1)
                return nullptr;

            CPUMDescriptor pmd;

            // Set desired model type to the "minimal model"
            pmd.type = CPUModelMinimalParams::key;

            // Start with maximum OPP as current opp (useless, this class and
            // the CPU implementation will always query CPUModel classes as
            // stateless functions)
            auto &curr_opp = opps[opps.size() - 1];
            return CPUModel::create(pmd, pmd, curr_opp, curr_opp.frequency)
                .release();
        }

        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        // NOTE: for now, CPUs will have to be created elsewhere

        // TODO: do I need powersaving flag?
        // TODO: document parameters
        CPUIsland(const std::vector<CPU *> &cpus = {},
                  Type type = Type::GENERIC, std::string name = "",
                  const std::vector<OPP> &opps = {},
                  CPUModel *powermodel = nullptr) :
            Entity(type.toString() + "_" + name),
            _type(type),
            _cpus(),
            _opps(opps),
            _powermodel(powermodel ? powermodel
                                   : createMinimalPowerModel(_opps)),
            _current_opp(opps.size() ? opps.size() - 1 : 0) {
            // Additional operations needed
            for (auto cpu : cpus) {
                addCPU(cpu);
            }
        }

        // TODO: document parameters
        CPUIsland(const std::vector<CPU *> &cpus = {},
                  Type type = Type::GENERIC, std::string name = "",
                  const std::vector<volt_type> &V = {},
                  const std::vector<freq_type> &F = {},
                  CPUModel *powermodel = nullptr) :
            CPUIsland(cpus, type, name, OPP::fromVectors(V, F), powermodel) {}

        CPUIsland(size_t num_cpus, Type type = Type::GENERIC,
                  std::string name = "", const std::vector<OPP> &opps = {},
                  CPUModel *powermodel = nullptr);

        CPUIsland(size_t num_cpus, Type type = Type::GENERIC,
                  std::string name = "", const std::vector<volt_type> &V = {},
                  const std::vector<freq_type> &F = {},
                  CPUModel *powermodel = nullptr) :
            CPUIsland(num_cpus, type, name, OPP::fromVectors(V, F),
                      powermodel) {}

        DISABLE_COPY(CPUIsland);
        DISABLE_MOVE(CPUIsland);

        // Move construction (warning: pointers to the rhs
        // object will be invalidated)
        // CPUIsland(CPUIsland &&rhs) :
        //     Entity(rhs.getName()),
        //     _type(std::move(rhs._type)),
        //     _powermodel(std::move(rhs._powermodel)),
        //     _opps(std::move(rhs._opps)),
        //     _current_opp(std::move(rhs._current_opp)),
        //     _frequency_switches(std::move(rhs._frequency_switches)) {
        //     // This should remove all CPUs from rhs and add them to this
        //     island for (auto cpu : rhs._cpus) {
        //         cpu->setIsland(this);
        //     }
        // }

        // CPUIsland & operator=(CPUIsland &&rhs) {
        //     Entity::operator=(std::move(rhs));
        //     _type = std::move(rhs._type);
        //     _powermodel = std::move(rhs._powermodel);
        //     _opps = std::move(rhs._opps);
        //     _current_opp = std::move(rhs._current_opp);
        //     _frequency_switches = std::move(rhs._frequency_switches);
        //     // This should remove all CPUs from rhs and add them to this
        //     island for (auto cpu : rhs._cpus) {
        //         cpu->setIsland(this);
        //     }
        // }

        virtual ~CPUIsland();

        // =================================================
        // Methods
        // =================================================
    public:
        // GETTERS
        Type type() const {
            return _type;
        }

        size_t getProcessorsNumber() const {
            return _cpus.size();
        }

        std::vector<CPU *> getProcessors() const {
            return std::vector<CPU *>(_cpus.cbegin(), _cpus.cend());
        }

        std::vector<OPP> getOPPs() const {
            return _opps;
        }

        // TODO: rename to getNumOPP
        size_t getOPPsize() const {
            return _opps.size();
        }

        /// @return the index of the current OPP
        size_t getOPPIndex() const {
            return _current_opp;
        }

        /// @return the OPP (freq+volt) from the given index
        OPP getOPP(size_t opp_index) const {
            assert(opp_index < getOPPsize());
            return _opps[opp_index];
        }

        /// @return the current OPP (freq+volt)
        OPP getOPP() const {
            return getOPP(getOPPIndex());
        }

        /// @return the frequency associated to the OPP with the given index
        freq_type getFrequency(size_t opp_index) const {
            assert(opp_index < getOPPsize());
            return getOPP(opp_index).frequency;
        }

        /// @return the voltage associated to the OPP with the given index
        volt_type getVoltage(size_t opp_index) const {
            assert(opp_index < getOPPsize());
            return getOPP(opp_index).voltage;
        }

        /// @return the current frequency
        freq_type getFrequency() const {
            return getFrequency(getOPPIndex());
        }

        /// @return the current voltage
        volt_type getVoltage() const {
            return getVoltage(getOPPIndex());
        }

        /// @return the index of the OPP associated with the
        /// given frequency or an out of bounds index if not
        /// found
        size_t getOPPIndexByFrequency(freq_type frequency) const {
            assert(frequency > 0);
            for (size_t i = 0; i < _opps.size(); ++i) {
                if (_opps[i].frequency == frequency)
                    return i;
            }

            // Error, not found (original code had an abort() here)
            return _opps.size();
        }

        /// @return the index of the given OPP (checks only
        /// if frequency matches)
        size_t getOPPIndexByOPP(const OPP &opp) const {
            return getOPPIndexByFrequency(opp.frequency);
        }

        /// @return a copy of all OPPs higher than *or equal to* the given one
        std::vector<OPP> getHigherOPPs(size_t opp_index) const {
            assert(opp_index < getOPPsize());
            std::vector<OPP> slice(_opps.cbegin() + opp_index, _opps.cend());
            return slice;
        }

        /// @return a copy of all OPPs higher than *or equal to* the current one
        std::vector<OPP> getHigherOPPs() const {
            return getHigherOPPs(getOPPIndex());
        }

        /// @return the CPUModel related to CPUs in this island
        const CPUModel *getCPUModel() const {
            return _powermodel;
        }

        /// @return the power consumption of the whole island
        watt_type getPower() const;

        size_t getFrequencySwitches() const {
            return _frequency_switches;
        }

        /// @returns whether at least one CPU on the island is busy
        bool busy() const;

        std::string toString() const override {
            return getName() + " freq " + std::to_string(getFrequency());
        }

        // NON-CONST METHODS

        // TODO: What is the purpose of these two methods?
        void newRun() override {
            // FIXME: reset the original OPP of the island,
            // set before the simulation started for the
            // first time... somehow!

            updateModels();
            _frequency_switches = 0;
        }
        void endRun() override {}

        // TODO: to be used only during system initialization
        bool addCPU(CPU *cpu);

        /// Used by CPU destructor (necessary to avoid UB
        /// when dereferencing dead CPUs)
        bool removeCPU(CPU *cpu);

        /// Sets the current OPP from index
        // TODO: track the number of frequency switches directly from here?
        void setOPP(size_t opp_index) {
            assert(opp_index < _opps.size());

            if (_current_opp != opp_index) {
                ++_frequency_switches;
                _current_opp = opp_index;
                updateModels();
            }
        }

    private:
        /// Updates the power and speed estimations on each
        /// CPU after a change of OPP in the island.
        void updateModels();

        // =================================================
        // Data
        // =================================================
    private:
        Type _type;

        CPUModel *_powermodel;

        std::set<CPU *> _cpus;

        std::vector<OPP> _opps;

        size_t _current_opp;

        size_t _frequency_switches = 0;
    };
} // namespace RTSim

////////////////////////////////////////////////////////////////////////////////
////////////////////////////////////////////////////////////////////////////////

#include <metasim/entity.hpp>
#include <metasim/simul.hpp>

#include <rtsim/class_utils.hpp>
#include <rtsim/opp.hpp>
#include <rtsim/powermodel.hpp>
#include <rtsim/system_descriptor.hpp>

#include <string>
#include <vector>

#include <cassert>

namespace RTSim {
    using namespace MetaSim;

    class CPU;

    // THIS IS NEEDEED BECAUSE USING A CPU POINTER TO INSTANTIATE A NEW VECTOR
    // IS MISTAKENLY INTERPRETED AS ALLOCATING A HUGE VECTOR (ADDRESS IS
    // INTERPRETED AS INTEGER!!)
    static inline std::vector<CPU *> _vector_from_cpu_pointer(CPU *c) {
        std::vector<CPU *> v;
        v.push_back(c);
        return v;
    }

    // =========================================================================
    // class CPU
    // =========================================================================

    ///
    class CPU : public Entity {
        // =================================================
        // Friend declarations
        // =================================================

        // Needed only because the updateModel() method is marked as private
        friend CPUIsland;

        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        /// @todo: documentation
        CPU(const std::string &name, CPUIsland *island) :
            Entity(name),
            _index(0),
            _workload("idle"),
            _island(nullptr) {
            // This invocation here is deeply problematic,
            // invoke this from outside right after creating
            // the CPU!

            // setIsland(island);
        }

        // /// Constructs a new CPU (also default constructor)
        // ///
        // /// V and F must have the same length.
        // ///
        // /// If an empty V/F is supplied, then power
        // /// management is disabled. If no power model is
        // /// supplied [...]
        // ///
        // /// @param name the name of this CPU
        // /// @param V list of voltages, one per OPP (asc order)
        // /// @param F list of frequencies, one per OPP (asc order)
        // /// @param pm pointer the power model uniquely associated to this CPU
        // ///
        // /// @todo: change to list of OPPs directly?
        // ///
        // /// @note this constructor, which does not have an
        // /// associated island, implicitly creates a new
        // /// island with the given parameters. Memory
        // /// management for these structures will be the
        // /// subject of future fixes, for now they stay
        // /// hanging in the heap forever. I know it is bad,
        // /// but for single-run applications it's not much of
        // /// a deal (unless a CPUIsland is then added to the
        // /// CPU, in that case the "zombie" CPUIsland created
        // /// by this method will keep existing forever...).
        // CPU(const std::string &name = "", const std::vector<volt_type> &V =
        // {},
        //     const std::vector<freq_type> &F = {}, CPUModel *pm = nullptr) :
        //     CPU(name, new CPUIsland(_vector_from_cpu_pointer(this),
        //                             CPUIsland::Type::GENERIC, name, V, F,
        //                             pm)) {
        // }

        DISABLE_COPY(CPU);
        DISABLE_MOVE(CPU);

        virtual ~CPU() {
            // Remove association in associated CPUIsland
            // (otherwise UB may happend when dereferencing
            // link to this CPU)
            auto island = getIsland();
            if (island) {
                island->removeCPU(this);
            }
        }

        // =================================================
        // Methods
        // =================================================
    public:
        // GETTERS

        /// Get the processor index
        int getIndex() const {
            return _index;
        }

        /// Get the associated CPUIsland
        CPUIsland *getIsland() const {
            return _island;
        }

        RTKernel *getKernel() const {
            return _kernel;
        }

        void setKernel(RTKernel *kernel) {
            _kernel = kernel;
        }

        /// Disabled CPUs should NOT be assigned tasks by schedulers.
        ///
        /// Mainly used for debugging purposes.
        ///
        /// @returns whether the CPU is disabled.
        bool disabled() const {
            return _disabled;
        }

        /// @returns whether the CPU is enabled.
        bool enabled() const {
            return !_disabled;
        }

        /// @note disabled CPUs are considered not busy!
        /// @return whether the CPU is busy
        // TODO: do we need a separed setBusy method?
        bool busy() const {
            return !disabled() && getWorkload() != "idle";
        }

        /// @note forwards to the linked CPUIsland
        std::vector<OPP> getOPPs() const {
            auto island = getIsland();
            if (!island)
                return {};
            return island->getOPPs();
        }

        /// @note forwards to the linked CPUIsland
        // TODO: change name to getOPPIndex, like in CPUIsland
        size_t getOPPIndex() const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getOPPIndex();
        }

        /// @note forwards to the linked CPUIsland
        // TODO: change name to getOPPIndex, like in CPUIsland
        OPP getOPP() const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getOPP();
        }

        /// @note forwards to the linked CPUIsland
        // TODO: change name to getOPPIndex, like in CPUIsland
        OPP getOPP(size_t opp_index) const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getOPP(opp_index);
        }

        std::string getWorkload() const {
            // TODO: check if this is ok
            if (disabled() || _workload.length() < 1)
                return "idle";
            return _workload;
        }

        /// @note forwards to the linked CPUIsland
        freq_type getFrequency(size_t opp_index) const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getFrequency(opp_index);
        }

        /// @note forwards to the linked CPUIsland
        volt_type getVoltage(size_t opp_index) const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getVoltage(opp_index);
        }

        /// @note forwards to the linked CPUIsland
        freq_type getFrequency() const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getFrequency();
        }

        /// @note forwards to the linked CPUIsland
        volt_type getVoltage() const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getVoltage();
        }

        /// @note forwards to the linked CPUIsland
        size_t getFrequencySwitches() const {
            auto island = getIsland();
            if (!island)
                return 0;
            return island->getFrequencySwitches();
        }

        /// @return the current speed at which the workload
        /// is running
        speed_type getSpeed() const {
            // TODO: check if this is ok
            if (disabled())
                return 0;
            return _cpu_speed;
        }

        /// @returns the current power consumption of this
        /// CPU
        watt_type getPower() const {
            return _cpu_power;
        }

        /// @returns the maximum power consumption
        /// obtainable on this CPU
        ///
        /// @todo: who provides this information?
        watt_type getPowerMax() const {
            return _max_power;
        }

        /// @return a copy of all OPPs higher than *or equal to* the given one
        std::vector<OPP> getHigherOPPs(size_t opp_index) const {
            auto island = getIsland();
            if (!island)
                return {};
            return island->getHigherOPPs(opp_index);
        }

        /// @return a copy of all OPPs higher than *or equal to* the current one
        std::vector<OPP> getHigherOPPs() const {
            auto island = getIsland();
            if (!island)
                return {};
            return island->getHigherOPPs();
        }

        std::string toString() const override {
            return getName() + " freq " + std::to_string(getFrequency());
        }

        // ---------- Templated Methods to avoid code repetitions ----------- //
    private:
        template <class retT>
        using cpumodel_method = retT (CPUModel::*)(const OPP &,
                                                   const std::string &) const;

        template <class retT>
        retT getValueByOPP(cpumodel_method<retT> lookup, size_t opp_index,
                           const std::string &workload) const {
            auto island = getIsland();
            if (!island)
                return 0;

            if (opp_index == island->getOPPsize())
                return 0;

            auto powermodel = island->getCPUModel();
            if (!powermodel)
                return 0;

            return (powermodel->*lookup)(island->getOPP(opp_index), workload);
        }

        // ------------------------- Speed Getters -------------------------- //
    public:
        /// @returns the speed of the current workload when
        /// running at the given OPP
        speed_type getSpeedByOPP(size_t opp_index,
                                 const std::string &workload) const {
            return getValueByOPP(&CPUModel::lookupSpeed, opp_index, workload);
        }

        /// @returns the speed of the current workload when
        /// running at the given frequency
        // TODO: change to getSpeedByFrequency
        speed_type getSpeed(freq_type frequency,
                            const std::string &workload) const {
            auto island = getIsland();
            if (!island)
                return 0;
            auto opp_index = island->getOPPIndexByFrequency(frequency);
            return getSpeedByOPP(opp_index, workload);
        }

        /// @returns the speed of the current workload when
        /// running at the given OPP
        speed_type getSpeedByOPP(size_t opp_index) const {
            return getSpeedByOPP(opp_index, getWorkload());
        }

        /// @returns the speed of the current workload when
        /// running at the given frequency
        // TODO: change to getSpeedByFrequency
        speed_type getSpeed(freq_type frequency) const {
            return getSpeed(frequency, getWorkload());
        }

        // ------------------------- Power Getters -------------------------- //
    public:
        /// @returns the power consumption of the current
        /// workload when running at the given OPP; the
        /// returned value is the contribution of this CPU
        /// only to the island power consumption
        watt_type getPowerByOPP(size_t opp_index,
                                const std::string &workload) const {
            auto island = getIsland();
            if (!island)
                return 0;

            auto num_cpus = island->getProcessorsNumber();

            // It is must must be at least one, because this
            // CPU is linked to the CPUIsland
            assert(num_cpus > 0);

            // The power model returns for each task the
            // power consumption of the whole island when
            // only one instance of that task is running.
            // This means that:
            //  - for the idle task, all CPUs are idle;
            //  - for active tasks, its active contribution
            //    is the difference between the whole idle
            //    consumption of the Island and the value
            //    returned by the power model.

            // This behavior is due to compatibility with
            // legacy models (the minimal and the BP model
            // in particular), the TB model can be
            // configured in any way depending on the
            // implementation of the tool that produces its
            // table, but it must be consistent with the
            // other models, so we use this convention.

            if (workload == "idle") {
                // Shortcut, the standard path gets the same
                // result for the "idle" state with double
                // the lookup cost and computation
                // (increasing error due to floating point
                // approximations)
                auto island_idle_power =
                    getValueByOPP(&CPUModel::lookupPower, opp_index, "idle");
                return island_idle_power / watt_type(num_cpus);
            }

            auto island_idle_power =
                getValueByOPP(&CPUModel::lookupPower, opp_index, "idle");
            auto cpu_active_power =
                getValueByOPP(&CPUModel::lookupPower, opp_index, workload);
            auto cpu_idle_power = island_idle_power / watt_type(num_cpus);
            // auto cpu_active_power = island_active_power - island_idle_power;
            return cpu_idle_power + cpu_active_power;
        }

        /// @returns the power consumption of the current workload when
        /// running at the given frequency
        // TODO: change to getPowerByFrequency
        watt_type getPower(freq_type frequency,
                           const std::string &workload) const {
            auto island = getIsland();
            if (!island)
                return 0;
            auto opp_index = island->getOPPIndexByFrequency(frequency);
            return getPowerByOPP(opp_index, workload);
        }

        /// @returns the power consumption of the current workload when
        /// running at the given OPP
        watt_type getPowerByOPP(size_t opp_index) const {
            return getPowerByOPP(opp_index, getWorkload());
        }

        /// @returns the power consumption of the current workload when
        /// running at the given frequency
        // TODO: change to getPowerByFrequency
        watt_type getPower(freq_type frequency) const {
            return getPower(frequency, getWorkload());
        }

        // -------------------------- Power Saving -------------------------- //

        /// How much power is saved when that much power is
        /// consumed
        ///
        /// @returns a fractional value that indicates how
        /// much power is saved when the given power is
        /// consumed
        long double getPowerSaving(long double power) const {
            long double p_max = getPowerMax();

            // TODO: check if this condition is correct
            if (p_max == 0 || power == 0)
                return 0;

            long double p_saved = p_max - power;
            return p_saved / p_max;
        }

        /// How much power is saved in this system condition
        ///
        /// @returns a fractional value that indicates how
        /// much the CPU is currently saving with respect to
        /// the maximum power consumption value.
        long double getPowerSaving() const {
            return getPowerSaving(getPower());
        }

        /// Performs a check on the power saving if the
        /// given opp is selected
        ///
        /// @see getPowerSaving
        long double getPowerSavingByOPP(size_t opp_index) const {
            return getPowerSaving(getPowerByOPP(opp_index));
        }

        /// Performs a check on the power saving if the
        /// given frequency is selected
        ///
        /// @see getPowerSaving
        long double getPowerSavingByFrequency(freq_type frequency) const {
            return getPowerSaving(getPower(frequency));
        }

        // NON-CONST METHODS

        // FIXME: reset counters etc.
        void newRun() override {
            setWorkload("idle");
        }

        void endRun() override {}

        /// Set the processor index
        void setIndex(int i) {
            _index = i;
        }

        /// Sets the island to which this CPU belongs
        bool setIsland(CPUIsland *island) {
            // NOTICE: order of these operations is
            // important to avoid infinte loop/stack
            // overflow!
            if (_island == island)
                return false;

            _island = island;
            if (_island) {
                // If island set to nullptr do not re-add
                // the CPU to the island set!
                _island->addCPU(this);
            }

            // Update in either case, so that speed/power
            // get set to zero if the island is no more
            // present
            updateModel();

            return true;
        }

        /// Disables current CPU.
        void disable() {
            _disabled = true;
        }

        /// Enables current CPU.
        void enable() {
            _disabled = false;
        }

        /// Set the workload currently running on the CPU
        void setWorkload(std::string workload) {
            assert(!disabled());
            _workload = workload;
            updateModel();
        }

        /// Sets the current OPP of the CPU using its index
        void setOPP(size_t opp_index) {
            auto island = getIsland();
            assert(island);
            // This will in turn update all models in each CPU of the island
            island->setOPP(opp_index);
        }

        /// Sets the maximum power consumption obtainable on this CPU
        /// @todo: who provides this information?
        void setPowerMax(watt_type max_p) {
            _max_power = max_p;
        }

    private:
        /// This method is used only for caching purposes.
        /// If this is deemed unnecessary, these operations
        /// can be distributed across const getters without
        /// any issue.
        void updateModel() {
            auto island = getIsland();
            if (!island) {
                // Reset values in case the association with
                // the Island has just been deleted.
                _cpu_power = 0;
                _cpu_speed = 0;
                return;
            }

            auto opp_index = island->getOPPIndex();
            auto workload = getWorkload();

            _cpu_power = getPowerByOPP(opp_index, workload);
            _cpu_speed = getSpeedByOPP(opp_index, workload);
        }

        // =================================================
        // Data
        // =================================================
    private:
        /// Index of the CPU in its multiprocessor environment
        int _index;

        /// Workload currently executing on this CPU ("idle"
        /// if no task is running)
        std::string _workload;

        /// Power consumption of this CPU in current working
        /// conditions (cached from CPUModel)
        watt_type _cpu_power = 0;

        /// Speed at which the current task is running
        speed_type _cpu_speed = 0;

        /// Maximum power consumption value registered
        /// throughout the CPU lifetime
        ///
        /// @todo set externally but never used? check again
        watt_type _max_power = 0;

        /// Disables the CPU, so that schedulers will not
        /// assign tasks to it until it is set enabled
        /// again.
        ///
        /// Used in debug only.
        ///
        /// Power consumed by disabled CPUs will be set
        /// equal to the idle power.
        // TODO: check if all schedulers abide by this rule
        bool _disabled = false;

        /// Current state of the CPU
        /// Invariant: _busy = (!_disabled && getWorkload() != "idle")
        /// @deprecated
        // bool _busy = false;

        /// Count of all frequency switch operations
        /// occurred throughout the CPU lifetime
        /// @deprecated moved to CPUIsland
        // unsigned long int frequencySwitching;

        /// Speed and Power Model associated to this CPU
        /// @deprecated
        // CPUModel *powmod;

        /// List of all OPPs available for this CPU
        /// @deprecated
        // std::vector<OPP> OPPs;

        /// List of all speeds calculated for this CPU and relative to the
        /// current workload
        /// @deprecated
        // std::vector<speed_type> speeds;

        /// Name of the CPU
        /// @deprecated
        // std::string cpuName;

        /// Index of the current OPP in the OPPs list
        /// @deprecated
        // std::vector<OPP>::size_type currentOPP;

        /// Determines whether power saving is enabled or not
        /// @deprecated
        // bool PowerSaving;

        /// Points to the kernel associated with this CPU (warning: do NOT move
        /// CPUs around from one kernel to another!)
        RTKernel *_kernel = nullptr;

        /// Island related to this CPU
        ///
        /// NOTE: IN CURRENT IMPLEMENTATION, MUST BE
        /// THE LAST ATTRIBUTE!! CONSTRUCTORS CALL EACH OTHER (I KNOW, BAD)
        /// EXPECTING THAT OTHER ATTRIBUTES ARE ALREADY INITIALIZED. BY THE
        /// STANDARD, ATTRIBUTES ARE INITIALIZED IN DECLARATION ORDER!!!
        CPUIsland *_island;
    };

    inline void CPUIsland::updateModels() {
        // NOTICE: The power model is held by the
        // CPUIsland, but always queried by each CPU
        if (_powermodel == nullptr)
            return;

        for (auto cpu : _cpus) {
            cpu->updateModel();
        }
    }

    inline CPUIsland::~CPUIsland() {
        // Remove association in associated CPUs
        // (otherwise UB may happend when dereferencing
        // link to this CPUIsland)

        // TODO: if CPUs are managed by the island, destroy them too
        for (auto cpu : _cpus) {
            cpu->setIsland(nullptr);
        }
    }

    inline watt_type CPUIsland::getPower() const {
        if (_powermodel == nullptr)
            return 0;

        // Get idle power from the linked power model
        watt_type power_cons = 0;

        // Each CPU is supposed to have a clone of the
        // given power model. The implementation of each
        // CPU returns the power consumed correctly,
        // i.e. including the idle component, but only
        // related to that particular CPU (not entire
        // island)
        for (auto cpu : _cpus) {
            power_cons += cpu->getPower();
        }

        return power_cons;
    }

    inline CPUIsland::CPUIsland(size_t num_cpus, Type type, std::string name,
                                const std::vector<OPP> &opps,
                                CPUModel *powermodel) :
        CPUIsland(std::vector<CPU *>{}, type, name, opps, powermodel) {
        for (size_t i = 0; i < num_cpus; ++i) {
            // This constructor will automatically add the
            // CPU to the set of this island and avoid
            // allocating a new CPUIsland for nothing.
            auto c = new CPU("", this);
            c->setIndex(i);
        }
    }

    inline bool CPUIsland::removeCPU(CPU *cpu) {
        auto res = _cpus.find(cpu);
        if (res == _cpus.cend())
            return false;

        // Remove the association of the CPU to the Island

        // TODO: should a CPU create a new Island each
        // time it is set to nullptr?
        (*res)->setIsland(nullptr);
        _cpus.erase(res);
        return true;
    }

    inline bool CPUIsland::addCPU(CPU *cpu) {
        // NOTICE: order of these operations is
        // important to avoid infinte loop/stack
        // overflow!

        auto res = _cpus.insert(cpu);

        if (res.second) {
            // This operation will trigger an update of the CPU model
            cpu->setIsland(this);
        }

        return res.second;
    }

    inline bool CPUIsland::busy() const {
        for (auto cpu : _cpus) {
            if (cpu->busy())
                return true;
        }
        return false;
    }
} // namespace RTSim

#include <queue>

// TODO: These factories should be updated to the new CPUs
namespace RTSim {
    // =========================================================================
    // class absCPUFactory
    // =========================================================================

    /// The abstract CPU factory.
    ///
    /// Base class for each CPU factory to be implemented.
    class absCPUFactory {
        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        DEFAULT_VIRTUAL_DES(absCPUFactory);

        // =================================================
        // Methods
        // =================================================
    public:
        /// Constructs a new CPU
        ///
        /// See CPU::CPU for more details about its
        /// parameters and behavior.
        virtual CPU *createCPU(const std::string &name = "",
                               const std::vector<volt_type> &V = {},
                               const std::vector<freq_type> &F = {},
                               CPUModel *pm = nullptr) = 0;
    };

    // =========================================================================
    // class uniformCPUFactory
    // =========================================================================

    /// A CPU factory that creates uniform CPUs, that is,
    /// CPUs from a fixed set of names, provided upon
    /// initialization of this factory.
    ///
    /// If more CPUs than the size of the names set on
    /// initialization is created, the name parameter will
    /// be used instead to set each CPU name.
    ///
    /// This class manages CPU indexes autonomously.
    class uniformCPUFactory : public absCPUFactory {
        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        /// Creates a new uniformCPUFactory that will use
        /// the given names to create the first n CPUs.
        ///
        /// CPUs after the nth will use the name parameter
        /// to be initializated.
        ///
        /// @param names list of names to be used to
        /// allocate the first n CPUs
        /// @param n the length of the names array
        uniformCPUFactory(std::string names[], int n);

        /// Default constructor, same as providing no names
        /// to the other constructor.
        uniformCPUFactory() : uniformCPUFactory(nullptr, 0) {}
        DEFAULT_VIRTUAL_DES(uniformCPUFactory);

        // =================================================
        // Methods
        // =================================================
    public:
        /// Constructs a new CPU
        ///
        /// See CPU::CPU for more details about its
        /// parameters and behavior.
        ///
        /// In addition, the name parameter is ignored for
        /// the first n calls, with n specified on creation
        /// of this factory.
        virtual CPU *createCPU(const std::string &name = "",
                               const std::vector<volt_type> &V = {},
                               const std::vector<freq_type> &F = {},
                               CPUModel *pm = nullptr) override;

        // =================================================
        // Data
        // =================================================
    private:
        // TODO: document attributes
        /// Names of all allocated CPUs
        std::vector<std::string> _names;

        /// Counts how many CPUs with fixed names have
        /// already been created
        int _count;

        /// Index for each CPU
        int _index;
    };

    inline uniformCPUFactory::uniformCPUFactory(std::string names[], int n) :
        _count(0),
        _index(0),
        _names(n) {
        for (size_t i = 0; i < _names.size(); ++i) {
            _names[i] = names[i];
        }
    }

    // inline CPU *uniformCPUFactory::createCPU(const std::string &name,
    //                                          const std::vector<volt_type> &V,
    //                                          const std::vector<freq_type> &F,
    //                                          CPUModel *pm) {
    //     CPU *c = nullptr;

    //     if (_count >= _names.size()) {
    //         c = new CPU(name, V, F, pm);
    //     } else {
    //         c = new CPU(_names[_count++], V, F, pm);
    //     }

    //     c->setIndex(_index++);
    //     return c;
    // }

    // =========================================================================
    // class customCPUFactory
    // =========================================================================

    /// A CPU factory that instead of creating new CPUs on
    /// request stores a set of CPUs and provides then one
    /// at a time, until there are no CPUs left.
    class customCPUFactory : public absCPUFactory {
        // =================================================
        // Constructors and destructors
        // =================================================
    public:
        customCPUFactory() = default;
        DEFAULT_VIRTUAL_DES(customCPUFactory);

        // =================================================
        // Methods
        // =================================================
    public:
        /// Adds another CPU to the set
        void addCPU(CPU *c) {
            _cpus.push(c);
        }

        /// All parameters provided to this method are
        /// effectively ignored.
        ///
        /// @returns the pointer to one of the stored
        /// pre-allocated CPUs, if the set is not already
        /// empty, nullptr otherwise.
        virtual CPU *createCPU(const std::string &name = "",
                               const std::vector<double> &V = {},
                               const std::vector<unsigned int> &F = {},
                               CPUModel *pm = nullptr) override {
            if (_cpus.empty())
                return nullptr;

            auto ret = _cpus.front();
            _cpus.pop();
            return ret;
        }

        // =================================================
        // Data
        // =================================================
    private:
        /// The list of CPUs to choose from. It behaves like a FIFO.
        std::queue<CPU *> _cpus;
    };
} // namespace RTSim

namespace RTSim {
    // using CPU_BL = CPU;
    // using Island_BL = CPUIsland;
} // namespace RTSim

#endif // __RTSIM_CPUISLAND_HPP__
