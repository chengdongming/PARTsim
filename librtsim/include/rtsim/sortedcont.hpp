#ifndef __RTSIM_SORTEDCONT_H__
#define __RTSIM_SORTEDCONT_H__

#include <algorithm>
#include <functional>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace RTSim {

    // template <typename T> // declaration only for TD;
    // class TD;             // TD == "Type Displayer"

// For reference type
#define RETURN_CONST_CALL(method, ...)                                         \
    ({                                                                         \
        return const_cast<reference>(                                          \
            const_cast<const self_type *>(this)->method(__VA_ARGS__));         \
    })

// For iterators. This is okay because our iterators are
// LegacyRandomAccessIterators and thus they are O(1) to increment for an
// arbitrary number of values
#define RETURN_CONST_ITER_CALL(method, ...)                                    \
    ({                                                                         \
        auto res = const_cast<const self_type *>(this)->method(__VA_ARGS__);   \
        return this->begin() + (res - this->begin());                          \
    })

    /// This class maintains an ordered vector as long as:
    /// - no data is modified through lvalue references
    /// with a value that would invalidate the existing
    /// ordering (i.e. modification is acceptable as long
    /// as it does not break the relative ordering of the
    /// given element with respect to its siblings)
    /// - new data is inserted using the provided insert
    /// function
    template <class V, class Comp = std::less<V>>
    class sorted_vector {
    private:
        using container_type = std::vector<V>;
        using self_type = sorted_vector<V, Comp>;

    public:
        using value_type = typename container_type::value_type;
        using size_type = typename container_type::size_type;
        using difference_type = typename container_type::difference_type;
        using reference = typename container_type::reference;
        using const_reference = typename container_type::const_reference;
        using pointer = typename container_type::pointer;
        using const_pointer = typename container_type::const_pointer;
        using iterator = typename container_type::iterator;
        using const_iterator = typename container_type::const_iterator;
        using reverse_iterator = typename container_type::reverse_iterator;
        using const_reverse_iterator =
            typename container_type::const_reverse_iterator;

    public:
        sorted_vector(Comp less = Comp()) : _less(less){};

        //
        // Iterator stuff
        //

        iterator begin() {
            return _container.begin();
        }

        iterator end() {
            return _container.end();
        }

        const_iterator begin() const {
            return _container.begin();
        }

        const_iterator end() const {
            return _container.end();
        }

        const_iterator cbegin() const {
            return _container.cbegin();
        }

        const_iterator cend() const {
            return _container.cend();
        }

    private:
        std::vector<V> _container;
        const Comp _less;

    public:
        // =================================================
        // "Inexact" search
        // =================================================

        // Returns in logarithmic time an iterator to the first element such
        // that the following condition holds:
        //          !_less(*iter, value)
        // Returns end() if none satisfies the condition.
        const_iterator first_non_less(const_reference value) const {
            return std::lower_bound(this->begin(), this->end(), value, _less);
        }

        // =================================================
        // "Exact" search
        // =================================================

        // Returns in logarithmic time an iterator to the first element such
        // that the following condition holds:
        //          !_less(*iter, value) && !_less(value, *iter)
        // Returns end() if none satisfies the condition.
        const_iterator find(const_reference value) const {
            auto res = this->first_non_less(value);
            auto _end = this->end();
            if (res < _end && _less(value, *res)) {
                return _end;
            }
            return res;
        }

        // =================================================
        // Non const methods - defined by calling const implementations and
        // casting away constness from the returned iterator
        // =================================================

        // #define RETURN_CONST_CALL(method, ...)                                         \
//     ({                                                                         \
//         auto res = static_cast<const self_type>(this)->method(__VA_ARGS__);    \
//         return const_cast<decltype(res)>(res);                                 \
//     })

        iterator first_non_less(const_reference value) {
            RETURN_CONST_ITER_CALL(first_non_less, value);
        }

        iterator find(const_reference value) {
            RETURN_CONST_ITER_CALL(find, value);
        }

        // =================================================
        // Data access and insertion
        // =================================================

        reference front() {
            return _container.front();
        }

        reference back() {
            return _container.back();
        }

        pointer data() {
            return _container.data();
        }

        reference at_pos(size_type pos) {
            return _container.at(pos);
        }

        reference operator[](size_type pos) {
            return _container[pos];
        }

        iterator insert(const_reference value) {
            const_iterator position = first_non_less(value);
            return _container.insert(position, value);
        }

        const_reference front() const {
            return _container.front();
        }

        const_reference back() const {
            return _container.back();
        }

        const_pointer data() const {
            return _container.data();
        }

        const_reference at_pos(size_type pos) const {
            return _container.at(pos);
        }

        const_reference operator[](size_type pos) const {
            return _container[pos];
        }
    };

    // Functor class for comparing pairs based on their key only
    template <class K, class V, class comparator = std::less<K>>
    struct less_pair {
        using pair_type = typename std::pair<K, V>;

        less_pair(comparator less = comparator()) : _less(less) {}

        bool operator()(pair_type const &v1, pair_type const &v2) const {
            return _less(v1.first, v2.first);
        }

        const comparator _less;
    };

    template <class K, class V, class Comp = less_pair<K, V>>
    class sorted_map : public sorted_vector<std::pair<K, V>, Comp> {
    private:
        using pair_type = std::pair<K, V>;
        using parent_type = sorted_vector<pair_type, Comp>;
        using self_type = sorted_map<K, V, Comp>;

    public:
        using value_type = typename parent_type::value_type;
        using size_type = typename parent_type::size_type;
        using difference_type = typename parent_type::difference_type;
        using reference = typename parent_type::reference;
        using const_reference = typename parent_type::const_reference;
        using pointer = typename parent_type::pointer;
        using const_pointer = typename parent_type::const_pointer;
        using iterator = typename parent_type::iterator;
        using const_iterator = typename parent_type::const_iterator;
        using reverse_iterator = typename parent_type::reverse_iterator;
        using const_reverse_iterator =
            typename parent_type::const_reverse_iterator;

    public:
        sorted_map(const Comp &less = Comp()) : parent_type(less){};

    public:
        const_iterator first_non_less_key(const K &key) const {
            const auto tmp = std::make_pair(key, V());
            return this->first_non_less(tmp);
        }

        const_iterator find_from_key(const K &key) const {
            const auto tmp = std::make_pair(key, V());
            return this->find(tmp);
        }

        const_reference at_key(const K &key) const {
            auto res = find_from_key(key);
            if (res == this->end()) {
                throw std::out_of_range("Key not found: " + key);
            }
            return *res;
        }

        /*
        #define RETURN_CONST_CALL(method, ...) \
            do { \
                auto res = \
                    std::static_cast<const
        decltype(this)>(this)->method(__VA_ARGS__); \
                return std::const_cast<decltype(res)>(res); \ } while (0)
        */

        iterator first_non_less_key(const K &key) {
            RETURN_CONST_ITER_CALL(first_non_less_key, key);
        }

        iterator find_key(const K &key) {
            RETURN_CONST_ITER_CALL(find_from_key, key);
        }

        reference at_key(const K &key) {
            RETURN_CONST_CALL(at_key, key);
        }
    };

#undef RETURN_CONST_CALL
#undef RETURN_CONST_ITER_CALL

} // namespace RTSim

/*
// Usage Example
#include <iosfwd>

int main() {
    RTSim::sorted_map<int, int> map;

    map.insert(std::make_pair(1, 5));
    map.insert(std::make_pair(4, 4));
    map.insert(std::make_pair(3, 3));
    map.insert(std::make_pair(2, 2));
    map.insert(std::make_pair(7, 1));
    map.insert(std::make_pair(10, 0));

    for (auto v : map) {
        std::cout << v.first << ":" << v.second << std::endl;
    }

    {
        auto v = map.first_non_less_key(5);
        std::cout << v->first << ":" << v->second << std::endl;

        --v;
        std::cout << v->first << ":" << v->second << std::endl;
    }

    {
        auto v = map.find_key(10);
        std::cout << v->first << ":" << v->second << std::endl;
    }

    {
        auto v = map.at_key(4);
        std::cout << v.first << ":" << v.second << std::endl;
    }

    return EXIT_SUCCESS;
}
*/

#endif // __RTSIM_SORTEDCONT_H__
