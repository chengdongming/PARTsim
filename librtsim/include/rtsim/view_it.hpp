#ifndef RTSIM_VIEW_IT
#define RTSIM_VIEW_IT

#include <cstddef>
#include <type_traits>

namespace RTSim {
    template <class Fun>
    struct function_ptr_or_value {
        using type =
            typename std::conditional<std::is_function<Fun>::value,
                                      std::add_pointer_t<Fun>, Fun>::type;
    };

    template <class Fun>
    using function_ptr_or_value_t = typename function_ptr_or_value<Fun>::type;

    template <class Iter, class ViewFun>
    // TODO: implement either iterator concepts or tagging!
    class ViewIt {
    private:
        // using self_type = ViewIt<Iter, ViewFun>;
        using iterator_type = Iter;
        using view_type = ViewFun;

    public:
        ViewIt() = default;
        ViewIt(const iterator_type &it, const view_type &view) :
            _it(it),
            _view(view) {}

        auto operator*() {
            return _view(*_it);
        }

        ViewIt &operator++() {
            ++_it;
            return *this;
        }

        ViewIt operator++(int) {
            ViewIt prev(*this);
            ++_it;
            return prev;
        }

        bool operator!=(const ViewIt &oth) const {
            return !(*this == oth);
        }

        bool operator==(const ViewIt &oth) const {
            return _it == oth._it;
        }

    private:
        iterator_type _it;

        // Store a function pointer or a function-like value
        function_ptr_or_value_t<ViewFun> _view;
    };
} // namespace RTSim

#endif // RTSIM_MATCHING_IT
