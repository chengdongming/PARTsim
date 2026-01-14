#ifndef RTSIM_MAP_SINGLE_IT
#define RTSIM_MAP_SINGLE_IT

namespace RTSim {
    template <bool ChooseFirst, class T1, class T2>
    struct choose_type;

    template <class T1, class T2>
    struct choose_type<true, T1, T2> {
        using type = T1;
    };

    template <class T1, class T2>
    struct choose_type<false, T1, T2> {
        using type = T2;
    };

    template <bool ChooseFirst, class T1, class T2>
    using choose_type_t = typename choose_type<ChooseFirst, T1, T2>::type;

    template <class MapIt, bool ReturnFirst = true>
    // TODO: implement either iterator concepts or tagging!
    class MapSingleIt {
    private:
        using self_type = MapSingleIt<MapIt, ReturnFirst>;
        using iterator = MapIt;
        static constexpr bool return_first = ReturnFirst;

        using pair_type = typename std::iterator_traits<iterator>::value_type;
        using pair_first_type = typename pair_type::first_type;
        using pair_second_type = typename pair_type::second_type;

    public:
        using iterator_category = std::forward_iterator_tag;
        using difference_type = typename MapIt::difference_type;
        using value_type =
            choose_type_t<return_first, pair_first_type, pair_second_type>;
        using pointer = std::add_pointer_t<value_type>;
        using reference = std::add_lvalue_reference_t<value_type>;

    public:
        MapSingleIt() = default;

        MapSingleIt(const MapSingleIt &) = default;
        MapSingleIt(MapSingleIt &&) = default;

        MapSingleIt &operator=(const MapSingleIt &) = default;
        MapSingleIt &operator=(MapSingleIt &&) = default;

        MapSingleIt(const iterator &it) : _it(it) {}

        reference operator*() {
            if constexpr (return_first)
                return _it->first;
            else
                return _it->second;
        }

        MapSingleIt &operator++() {
            ++_it;
            return *this;
        }

        MapSingleIt operator++(int) {
            MapSingleIt prev(*this);
            ++_it;
            return prev;
        }

        bool operator!=(const self_type &oth) const {
            return !(*this == oth);
        }

        bool operator==(const self_type &oth) const {
            return _it == oth._it;
        }

    private:
        iterator _it;
    };
} // namespace RTSim

#endif // RTSIM_MAP_SINGLE_IT
