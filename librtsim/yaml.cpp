#include <rtsim/yaml.hpp>

namespace yaml {

    static inline size_type first_non_whitespace(const string &str) noexcept {
        return str.find_first_not_of(" ");
    }

    static inline size_type last_non_whitespace(const string &str) noexcept {
        return str.find_last_not_of(" ");
    }

    static inline bool contains(const string &str, const char &c) noexcept {
        return str.find(c) != string::npos;
    }

    static inline bool
        contains_forbidden_characters(const string &str) noexcept {
        return contains(str, '\t');
    }

    static inline string trim(const string &str) {
        const auto strBegin = first_non_whitespace(str);
        if (strBegin == std::string::npos)
            return ""; // no content

        const auto strEnd = last_non_whitespace(str);
        const auto strRange = strEnd - strBegin + 1;

        return str.substr(strBegin, strRange);
    }

    std::istream &Parser::getline(bool should_read, std::string &line) {
        if (!should_read)
            return is;

        std::istream &res = std::getline(is, unfinished_line);
        line = unfinished_line;
        ++parsing_line_number;
        return res;
    }

    Object_ptr Parser::parse(size_type base_indentation, string &line,
                             bool is_vector_traditional) {
        Object_ptr root = std::make_shared<Object>();

        bool already_incremented = false;
        bool should_read = false;

        // The should_read guard prevents the actual getline to be invoked if
        // there is still data left in line; in that case, line remains
        // untouched
        while (getline(should_read, line)) {
            should_read = true;

            if (contains_forbidden_characters(line)) {
                throw ParseException(
                    "Line " + std::to_string(parsing_line_number) +
                    " contains at least one forbidden character for this"
                    " implementation of YAML.\n"
                    " Line content: '" +
                    unfinished_line + "'");
            }

            // Delete everything after the comment character '#'
            size_type cbegin = line.find('#');
            line = line.substr(0, cbegin);

            // Skip empty lines
            size_type sbegin = first_non_whitespace(line);
            if (sbegin == string::npos)
                continue;

            // Expect at least base_indentation characters, otherwise we are
            // done
            if (sbegin != base_indentation && !is_vector_traditional) {
                if (sbegin > base_indentation) {
                    if (already_incremented) {
                        throw ParseException(
                            "Line " + std::to_string(parsing_line_number) +
                            " contains either too many or too few levels of"
                            " indentation, document is ill-formed for this"
                            " implementation of YAML.\n"
                            " Line content: '" +
                            unfinished_line + "'");
                    }

                    base_indentation = sbegin;
                    already_incremented = true;
                } else {
                    // I'm done parsing this sub-object
                    return root;
                }
            }

            // Strip line from beginning and finishing white spaces
            string content = trim(line);

            // If I don't know what kind of object is this one, let's check
            // now
            if (!(*root)) {
                if (content[0] == '-') {
                    root->setType(ObjType::Sequence);
                    if (is_vector_traditional)
                        throw ParseException(
                            "Line " + std::to_string(parsing_line_number) +
                            " contains a '-' character inside a traditional"
                            " sequence (which can only contain scalar values!),"
                            " document is ill-formed for this implementation of"
                            " YAML.\n"
                            " Line content: '" +
                            unfinished_line + "'");
                    // is_vector_traditional = false;
                } else if (content[0] == '[') {
                    root->setType(ObjType::Sequence);
                    is_vector_traditional = true;
                } else if (contains(content, ':')) {
                    root->setType(ObjType::Map);
                    // TODO: check if this COULD work
                } else {
                    root->setType(ObjType::Scalar);
                }
            }

            // Now that I know the type, I know what to look for
            switch (root->getType()) {
            case ObjType::Undefined:
            case ObjType::Null:
                throw ParseException("Unexpected error at line " +
                                     std::to_string(parsing_line_number) + "!");
            case ObjType::Scalar: {
                content = trim(content);
                string original_content = content;
                size_type last_content = content.size();
                size_type last_found = last_content;

                if (is_vector_traditional) {
                    for (last_content = content.find_first_of("],");
                         last_content <= content.size();
                         last_content = content.find_first_of("],")) {
                        last_found = last_content;
                        content = trim(content.substr(0, last_content));
                    }
                }

                // Consume the content and return
                root->get() = content;

                string remaining_line(base_indentation + 1, ' ');
                remaining_line += original_content.substr(last_found);

                line = remaining_line;
                return root;
            }
            case ObjType::Sequence: {
                size_type skip = 1;

                if (is_vector_traditional) {
                    // Terminate parsing if ] is found
                    if (content[0] == ']') {
                        string remaining_line(base_indentation + 1, ' ');
                        remaining_line += content.substr(1);
                        line = remaining_line;
                        return root;
                    }

                    if (content[0] != ',' && content[0] != '[')
                        throw ParseException(
                            "Line " + std::to_string(parsing_line_number) +
                            " contains an unexpected character inside a "
                            "sequence"
                            " of values, document is ill-formed for this"
                            " implementation of YAML.\n"
                            " Line content: '" +
                            unfinished_line + "'");

                    skip = 1;
                } else {
                    // Expect a -, otherwise return
                    if (content[0] != '-')
                        return root;
                }

                string remaining_line(base_indentation + 1, ' ');
                remaining_line += content.substr(skip);

                Object_ptr subObj = parse(base_indentation + 1, remaining_line,
                                          is_vector_traditional);
                if (remaining_line != "") {
                    line = remaining_line;
                    should_read = false;
                }
                root->push_back(subObj);
                break;
            }
            case ObjType::Map: {
                // Line must contain ':' character and must have at least a
                // string before that character
                size_type colon_pos = content.find(':');
                if (colon_pos == string::npos) {
                    // return root;
                    throw ParseException(
                        "Line " + std::to_string(parsing_line_number) +
                        " does not contain an expected ':' character!\n"
                        " Line content: '" +
                        unfinished_line + "'");
                }

                string attr = trim(content.substr(0, colon_pos));
                if (attr.length() < 1) {
                    throw ParseException(
                        "Line " + std::to_string(parsing_line_number) +
                        " contains an unexpected ':' character"
                        " where an attribute name was expected!\n"
                        " Line content: '" +
                        unfinished_line + "'");
                }

                // Remove the part before the : and sub-parse the line once
                // again
                string remaining_line(base_indentation + 1, ' ');
                remaining_line += content.substr(colon_pos + 1);

                Object_ptr subObj = parse(base_indentation + 1, remaining_line,
                                          is_vector_traditional);
                if (remaining_line != "") {
                    line = remaining_line;
                    should_read = false;
                }
                root->get(attr) = subObj;
                break;
            }
            } // end switch
        }

        // Extremely important! Do not forget!
        line = "";
        return root;
    }

} // namespace yaml

/*
// Example of use
#include <iostream>

#include <rtsim/yaml.hpp>

using std::string;

void print(yaml::Object_ptr objptr, yaml::size_type indent = 0) {
    switch (objptr->getType()) {
    case yaml::ObjType::Undefined:
    case yaml::ObjType::Null:
        return;
    case yaml::ObjType::Scalar: {
        std::string indents(indent, ' ');
        std::cout << indents << objptr->get() << std::endl;
        return;
    }
    case yaml::ObjType::Sequence: {
        std::string indents(indent, ' ');
        for (auto elem : *objptr) {
            std::cout << indents << "-" << std::endl;
            print(elem, indent + 2);
        }
        return;
    }
    case yaml::ObjType::Map: {
        std::string indents(indent, ' ');
        for (auto attr : objptr->get_attrs()) {
            std::cout << indents << attr.first << ":" << std::endl;
            print(attr.second, indent + 2);
        }
    }
    } // end switch
}

int main() {
    string fname = "src/examples/energy_table/odroid_bp.yml";
    yaml::Object_ptr document;

    try {
        document = yaml::parse(fname);
    } catch (std::exception &e) {
        std::cerr << "ERROR: Encountered an error while parsing YAML file "
                  << fname << std::endl;

        std::cerr << "CAUSE: " << e.what() << std::endl;
        return EXIT_FAILURE;
    }

    print(document);

    std::cout << "pm_descriptor: '" << document->get("pm_descriptor")->get()
              << "'" << std::endl;

    yaml::Object_ptr cpu_islands = document->get("cpu_islands");

    for (const auto &island : *cpu_islands) {
        std::cout << "------- CPU ISLAND -------" << std::endl;

        string numcpus = island->get("numcpus")->get();
        string pm_type = island->get("pm_type")->get();
        string scheduler = island->get("kernel")->get("scheduler")->get();
        string name = island->get("kernel")->get("name")->get();

        std::cout << "numcpus: '" << numcpus << "'" << std::endl;
        std::cout << "pm_type: '" << pm_type << "'" << std::endl;
        std::cout << "scheduler: '" << scheduler << "'" << std::endl;
        std::cout << "kernel name: '" << name << "'" << std::endl;
    }
    return EXIT_SUCCESS;
}
*/
