#ifndef CLASS_UTILS_H
#define CLASS_UTILS_H

// -------------------------------------------------------------------------- //
//         Macros that disable constructors and assignment operators          //
// -------------------------------------------------------------------------- //

// Explicitly disables DEFAULT CONSTRUCTION for the given class
#define DISABLE_DEFAULT_CONSTRUCTOR(Class) Class() = delete

// Explicitly disables COPY CONSTRUCTION and COPY ASSIGNMENT for the given class
// (implicitly disables move construction and move assignment if not otherwise
// specified)
#define DISABLE_COPY(Class)                                                    \
    Class(const Class &) = delete;                                             \
    Class &operator=(const Class &) = delete

// Explicitly disables MOVE CONSTRUCTION and MOVE ASSIGNMENT for the given class
#define DISABLE_MOVE(Class)                                                    \
    Class(Class &&) = delete;                                                  \
    Class &operator=(Class &&) = delete

// Explicitly disables constructors for the given class, namely it explicitly
// disables DEFAULT CONSTRUCTION, COPY CONSTRUCTION, and COPY ASSIGNMENT
// (implicitly disables move construction and move assignment if not otherwise
// specified)
#define DISABLE_IMPLICIT_CONSTRUCTIONS(Class)                                  \
    DISABLE_DEFAULT_CONSTRUCTOR(Class);                                        \
    DISABLE_COPY(Class)

// -------------------------------------------------------------------------- //
//          Macros that enable constructors and assignment operators          //
// -------------------------------------------------------------------------- //

// Enables back default COPY CONSTRUCTION and COPY ASSIGNMENT operations for the
// given class
#define DEFAULT_COPIABLE(Class)                                                \
    inline Class(const Class &) = default;                                     \
    inline Class &operator=(const Class &) = default

// Enables back default MOVE CONSTRUCTION and MOVE ASSIGNMENT operations for the
// given class
#define DEFAULT_MOVABLE(Class)                                                 \
    inline Class(Class &&) = default;                                          \
    inline Class &operator=(Class &&) = default

// Declares a VIRTUAL DESTRUCTOR for the given class using default compiler
// implementation
#define DEFAULT_VIRTUAL_DES(Class) inline virtual ~Class() = default

#endif // CLASS_UTILS_H
