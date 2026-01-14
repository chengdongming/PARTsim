#ifndef __RTSIM_CSV_HPP__
#define __RTSIM_CSV_HPP__

#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include <rtsim/trim.hpp>

// Known not supported CSV features:
// - quoted stuff
// - CR/LF and similar (simple line breaks used instead)
// - type conversions (class is all string-based)
// - Requires first row to be a header column
// - Only column names are supported, no row names (first column is always a
//      data column)
// - UTF-8/16/... data stuff
// - Assumes all columns have distinct names to them
// - Performs trim operations on all values

// TODO: noexcept to methods
// TODO: virtual all over the place

namespace csv {

    using std::ifstream;
    using std::istream;
    using std::istringstream;
    using std::string;
    using std::vector;

    using std::getline;

    static constexpr char csv_separator = ',';

    class CSVDocument {
    public:
        using column_type = vector<string>;
        using row_type = column_type;
        using size_type = column_type::size_type;
        using table_type = std::map<string, column_type>;
        using header_type = row_type; // Not very efficient

    public:
        // -----------------------------------------------------
        // Contructors
        // -----------------------------------------------------

        // Constructor accepting a rvalue reference, will simply forward the
        // value to the corresponding lvalue-based string constructor
        inline explicit CSVDocument(const string &&path) : CSVDocument(path) {}

        // Constructor accepting a lvalue reference
        inline explicit CSVDocument(const string &path) {
            if (!path.empty()) {
                readFile(path);
            }
        }

        // Constructor accepting a rvalue reference, will simply forward the
        // value to the corresponding lvalue-based istream constructor
        inline explicit CSVDocument(istream &&is) : CSVDocument(is) {}

        // Constructor accepting a lvalue reference
        inline explicit CSVDocument(istream &is) {
            readStream(is);
        }

        // Uses default constructor behavior
        inline CSVDocument() = default;

        // Can be both copy initialized and assigned using default behavior
        inline CSVDocument(const CSVDocument &) = default;
        inline CSVDocument &operator=(const CSVDocument &) = default;

        // Can be both move initialized and move assigned using default behavior
        inline CSVDocument(CSVDocument &&) = default;
        inline CSVDocument &operator=(CSVDocument &&) = default;

        // Can use default virtual destructor behavior
        virtual ~CSVDocument() = default;

    public:
        // -----------------------------------------------------
        // Public interface
        // -----------------------------------------------------

        inline virtual void clear() {
            CSVDocument empty;
            swap(empty);
        }

        inline virtual void swap(CSVDocument &other) {
            using std::swap;
            swap(this->_header, other._header);
            swap(this->_table, other._table);
        }

        inline virtual size_type columns() const {
            return _header.size();
        }
        inline virtual size_type rows() const {
            if (_table.size() < 1)
                return 0;
            return _table.cbegin()->second.size();
        }

        inline virtual size_type columns_size() const {
            return rows();
        }
        inline virtual size_type rows_size() const {
            return columns();
        }

        inline virtual const string at(string colname,
                                       size_type row_idx) const {
            auto res = _table.find(colname);
            if (res == _table.cend())
                return "";
            return res->second[row_idx];
        }

        /*
            inline string &at(string colname, size_type row_idx) {
                auto res = _table.find(colname);
                if (res == _table.cend())
                    return "";  // It would not work because potentially there
           is a
                                // out of bounds reference
            return res->second[row_idx];
            }
        */

        inline virtual const row_type &header() const {
            return _header;
        }

        inline virtual row_type getrow(size_type row_idx) const {
            row_type row(rows_size());
            size_type i = 0;
            for (auto cname = _header.cbegin(); cname != _header.cend();
                 ++cname, ++i)
                row[i] = at(*cname, row_idx);
            return row;
        }

        inline virtual const column_type &getcolumn(string colname) const {
            auto res = _table.find(colname);
            if (res == _table.cend())
                throw std::runtime_error(
                    "CSVDocument: Attempting to read non-existent column");
            return res->second;
        }

    protected:
        inline virtual void readFile(const string &&path) {
            readFile(path);
        }
        inline virtual void readFile(const string &path) {
            ifstream ifs{path};
            if (ifs.fail()) {
                throw std::runtime_error{"Attempting to read non-existent file: " +
                                     path};
            }

            // ifs.exceptions(ifstream::failbit | ifstream::badbit);
            // ifs.open(path); // , std::ios::binary
            readStream(ifs);
        }

        inline virtual void readStream(istream &&is) {
            readStream(is);
        }
        inline virtual void readStream(istream &is) {
            string buffer;
            string cell;

            // First, let's parse the header
            if (!getline(is, buffer)) {
                // TODO: some error occurred! Could't read even a single line!
            }

            // Read the header and create all columns in the table
            {
                istringstream iss(buffer);
                while (getline(iss, cell, csv_separator)) {
                    trim(cell);
                    auto res = _table.find(cell);
                    if (res != _table.cend()) {
                        // TODO: error! the key was already in the table!
                    }
                    _header.push_back(cell);
                    _table[cell] = {};
                }
            }

            // TODO: this will throw an out-of-range exception if the header is
            // not as long as the final file
            while (std::getline(is, buffer)) {
                int i = 0;
                istringstream iss(buffer);
                for (; getline(iss, cell, csv_separator); ++i) {
                    trim(cell);
                    const string &key = _header.at(i);
                    _table[key].push_back(cell);
                }

                cell = "";

                for (; i < _header.size(); ++i) {
                    // Technically, I can skip the boundary check here
                    const string &key = _header.at(i);
                    _table[key].push_back(cell);
                }
            }
        }

    protected:
        table_type _table;
        header_type _header;

        // TODO: separation and conversion stuff
    };

    static inline void swap(CSVDocument &a, CSVDocument &b) {
        a.swap(b);
    }

} // namespace csv

/*
// Usage:
#include <iosfwd>
#include <rtsim/csv.hpp>

int main() {
    std::string fname = "prova.csv";
    simplecsv::CSVDocument table{fname};

    auto &header = table.header();
    const auto rows = table.rows();

    for (auto cname = header.cbegin(); cname != header.cend(); ++cname) {
        std::cout << *cname << ",";
    }
    std::cout << std::endl;

    for (auto row_idx = decltype(rows){0}; row_idx < rows; ++row_idx) {
        for (auto cname = header.cbegin(); cname != header.cend(); ++cname) {
            std::cout << table.at(*cname, row_idx) << ",";
        }
        std::cout << std::endl;
    }
}
*/

#endif // __RTSIM_CSV_HPP__
