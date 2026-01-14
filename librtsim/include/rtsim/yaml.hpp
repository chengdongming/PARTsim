#ifndef YAML_H
#define YAML_H

#include <fstream>
#include <map>
#include <string>
#include <vector>
#include <utility>

#include <metasim/baseexc.hpp>
#include <metasim/memory.hpp>

// Known not supported YAML features:
// - basically everything related to JSON
// - | and < operators
// - objects on one line
// - objects enclosed in {}
// - SERIOUSLY: don't stress the capabilities of this implementation

// Source: https://stackoverflow.com/a/47369227/7030275
template <class T>
constexpr T &as_mutable(T const &value) noexcept {
    return const_cast<T &>(value);
}

template <class T>
constexpr T *as_mutable(T const *value) noexcept {
    return const_cast<T *>(value);
}

template <class T>
constexpr T *as_mutable(T *value) noexcept {
    return value;
}

// template <class T>
// constexpr std::shared_ptr<T>
//     as_mutable(const std::shared_ptr<const T> &value) noexcept {
//     return std::const_pointer_cast<T>(value);
// }

// template <class T>
// constexpr std::shared_ptr<T>
//     as_mutable(const std::shared_ptr<T> &value) noexcept {
//     return value;
// }

template <class T>
void as_mutable(T const &&) = delete;

namespace yaml {

    using std::ifstream;
    using std::istream;
    using std::map;
    using std::string;
    using std::vector;

    using std::shared_ptr;
    using size_type = size_t;

    using std::getline;

    static constexpr char symbol_array = '-';
    static constexpr char symbol_object = ':';

    enum class ObjType {
        Undefined = 0,
        Null,
        Scalar,
        Sequence,
        Map,
    };

    class Object;
    using Object_ptr = shared_ptr<Object>;
    using Object_ptr_const = shared_ptr<const Object>;

    // TODO: all sorts of error checking

    class Object {
    private:
        using sequence_type = vector<Object_ptr>;
        using map_type = map<string, Object_ptr>;

    public:
        using iterator = sequence_type::iterator;
        using const_iterator = sequence_type::const_iterator;
        using reverse_iterator = sequence_type::reverse_iterator;
        using const_reverse_iterator = sequence_type::const_reverse_iterator;

    public:
        // Constructors
        Object(ObjType type) : type(type) {}

        // Uses default constructor
        inline Object() noexcept = default;

        // Can be both copy initialized and assigned using default behavior
        inline Object(const Object &) noexcept = default;
        inline Object &operator=(const Object &) noexcept = default;

        // Can be both move initialized and move assigned using default behavior
        inline Object(Object &&) noexcept = default;
        inline Object &operator=(Object &&) noexcept = default;

        // Can use default virtual destructor behavior
        virtual ~Object() noexcept = default;

    public:
        inline virtual void clear() noexcept {
            Object empty;
            swap(empty);
        }

        inline virtual void swap(Object &other) noexcept {
            using std::swap;
            swap(type, other.type);
            swap(scalar, other.scalar);
            swap(list, other.list);
            swap(attrs, other.attrs);
        }

        inline ObjType getType() noexcept {
            return type;
        }

        inline virtual void setType(ObjType type) noexcept {
            if (this->type != type) {
                this->type = type;
                scalar = {};
                list = {};
                attrs = {};
            }
        }

        inline void virtual put(string s) noexcept {
            setType(ObjType::Scalar);
            scalar = s;
        }

        inline void virtual put(size_type index, Object_ptr obj) {
            setType(ObjType::Sequence);
            list[index] = obj;
        }

        inline void virtual put(const string &key, Object_ptr obj) {
            setType(ObjType::Map);
            attrs[key] = obj;
        }

        inline void virtual push_back(Object_ptr obj) {
            setType(ObjType::Sequence);
            list.push_back(obj);
        }

        inline virtual string &get() noexcept {
            return as_mutable(std::as_const(*this).get());
        }

        inline virtual const string &get() const noexcept {
            return scalar;
        }

        inline virtual Object_ptr &get(size_type index) {
            if (index >= list.size())
                throw MetaSim::BaseExc("YAML out of bounds exception!");
            return list[index];
        }

        inline virtual Object_ptr &get(const string &key) {
            if (!has(key))
                attrs[key] = std::make_shared<Object>();
            return attrs[key];
        }

        // // These two overloads give problems because we'd like to return
        // // pointers to consts, but we cannot, because vector is oblivious to
        // // the fact that we stored pointers in it. Same for the map.
        // inline virtual Object_ptr_const &get(size_type index) const {
        //     if (index >= list.size())
        //         throw MetaSim::BaseExc("YAML out of bounds exception!");
        //     return list[index];
        // }

        // inline virtual Object_ptr_const &get(const string &key) const {
        //     if (!has(key))
        //         throw MetaSim::BaseExc(
        //             "YAML requested unknown attribute from const!");
        //     return attrs[key];
        // }

        inline virtual const map_type &get_attrs() const {
            return attrs;
        }
        inline virtual map_type &get_attrs() {
            return attrs;
        }

        // To check whether the key is present
        inline virtual bool has(const string &key) const {
            auto res = attrs.find(key);
            return res != attrs.cend();
        }

        // Object_ptr &get(const char key[]) { return attrs[key]; }

        // Operators
        inline virtual operator bool() const noexcept {
            return type != ObjType::Undefined && type != ObjType::Null;
        }
        inline virtual bool operator!() const noexcept {
            return !operator bool();
        }

        // To iterate over sequences (arrays) of values
        inline virtual size_type size() const {
            return list.size();
        }

        inline virtual iterator begin() {
            return list.begin();
        }
        inline virtual iterator end() {
            return list.end();
        }
        inline virtual reverse_iterator rbegin() {
            return list.rbegin();
        }
        inline virtual reverse_iterator rend() {
            return list.rend();
        }

        inline virtual const_iterator cbegin() const {
            return list.cbegin();
        }
        inline virtual const_iterator cend() const {
            return list.cend();
        }
        inline virtual const_reverse_iterator crbegin() const {
            return list.crbegin();
        }
        inline virtual const_reverse_iterator crend() const {
            return list.crend();
        }

        /*
            string &operator*() { return get(); }
            Object_ptr &operator[](size_type index) { return get(index); }
            Object_ptr &operator[](const string &key) { return get(key); }
            Object_ptr &operator[](const string &&key) { return get(key); }
        */
    private:
        // Attributes
        ObjType type = ObjType::Undefined;

        // Undefined/Null
        // ...

        // Scalar
        string scalar;

        // Sequence
        sequence_type list;

        // Map
        map_type attrs;
    };

    inline void swap(Object &left, Object &right) {
        left.swap(right);
    }

    class ParseException : public std::exception {
    public:
        ParseException(const std::string &&message) :
            std::exception(),
            message(message) {}
        ParseException(const std::string &message) :
            std::exception(),
            message(message) {}

        // Does not use default constructor
        inline ParseException() = delete;

        // Can be both copy initialized and assigned using default behavior
        inline ParseException(const ParseException &) noexcept = default;
        inline ParseException &
            operator=(const ParseException &) noexcept = default;

        // Can be both move initialized and move assigned using default behavior
        inline ParseException(ParseException &&) noexcept = default;
        inline ParseException &operator=(ParseException &&) noexcept = default;

        const char *what() const noexcept override {
            return message.c_str();
        }

    private:
        std::string message;
    };

    // Pre-declare friend function
    static inline Object_ptr parse(const string &fname);

    class Parser {
    public:
        // Indicate that the friend function is indeed a friend
        friend Object_ptr parse(const string &fname);

    protected:
        Parser(std::istream &is) : is(is) {
            _root = parse();
        }
        virtual ~Parser() = default;
        Object_ptr get() noexcept {
            return _root;
        }

    protected:
        virtual std::istream &getline(bool should_read, std::string &line);

        inline virtual Object_ptr parse(size_type base_indentation = 0) {
            std::string line;
            return parse(base_indentation, line);
        }

        virtual Object_ptr parse(size_type base_indentation, string &line,
                                 bool is_vector_traditional = false);

    private:
        std::istream &is;
        size_type parsing_line_number;
        string unfinished_line;
        Object_ptr _root = nullptr;
    };

    // Define friend function
    Object_ptr parse(const string &fname) {
        std::ifstream ifs{fname};

        if (!ifs)
            throw ParseException("File " + fname + " could not be opened.");

        return Parser(ifs).get();
    }

} // namespace yaml

#endif // YAML_H
