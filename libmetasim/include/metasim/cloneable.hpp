#ifndef __CLONEABLE_HPP__
#define __CLONEABLE_HPP__

#include <metasim/memory.hpp>

// NOTICE: this assumes that the class is at least COPY CONSTRUCTIBLE (and has a
// virtual destructor, for the unique and shared pointers to work properly)

// Use this when you want to define a clone method as pure virtual.
#define BASE_CLONEABLE(Type) virtual std::unique_ptr<Type> clone() const = 0;

// This whole ordeal of various macros is needed to make the "Override" argument
// optional (one could simply use CLONEABLE_OVERRIDE without supplying any
// parameter (empty) for the Override argument, but it wouldn't be
// backwards-compatible)

// NOTICE: my original implementation used to rely on std::make_unique, but that
// works only if you use publicly available constructors. Since some classes of
// this project (for some reason or another) have private or protected copy
// constructors, that's not possble to use them. So, despite this being slightly
// less safe, this implementation relies now on simple constructors of unique
// pointers, in the hope that the compiler won't mess up when re-ordering
// instructions that may throw exceptions. NOTE: marking std::make_unique as
// friend functions does not work either

// I provide here the implementation that I wrote that used make_unique for
// completeness, in case somebody removes the private/protected (but
// not-deleted) copy constructors from those troublesome classes

// #define MAKE_UNIQUE(Type, this) std::make_unique<Type>(*this);

#define MAKE_UNIQUE(Type, this) std::unique_ptr<Type>(new Type(*(this)))

#define CLONEABLE_LEGACY(Base, Type)                                           \
    virtual std::unique_ptr<Base> clone() const {                              \
        return MAKE_UNIQUE(Type, this);                                        \
    }

#define CLONEABLE_OVERRIDE(Base, Type, Override)                               \
    virtual std::unique_ptr<Base> clone() const Override {                     \
        return MAKE_UNIQUE(Type, this);                                        \
    }

#define GET_4TH_ARG(arg1, arg2, arg3, arg4, ...) arg4
#define CLONEABLE_MACRO_CHOOSER(...)                                           \
    GET_4TH_ARG(__VA_ARGS__, CLONEABLE_OVERRIDE, CLONEABLE_LEGACY, )

#define CLONEABLE(...) CLONEABLE_MACRO_CHOOSER(__VA_ARGS__)(__VA_ARGS__)

// Usage:
// a) CLONEABLE(BaseType, CurrentType) -> Define a clone method that returns a
// unique_pointer to an element of the BaseType class, that points to an object
// of the CurrentType class.
// b) CLONEABLE(BaseType, CurrentType, override) -> Define a clone method that
// returns a unique_pointer to an element of the BaseType class, that points to
// an object of the CurrentType class, explicitly marking it as an override to a
// method of the base class.

#endif // __CLONEABLE_HPP__
