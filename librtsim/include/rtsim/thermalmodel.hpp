#include <Eigen/Dense>
#include <unsupported/Eigen/MatrixFunctions>
#include <vector>

class ThermalModel {
public:
    using scalar_type = double;

private:
    static_assert(
        std::numeric_limits<scalar_type>::has_infinity,
        "The type of scalar_type must be a type with an infinity value!");

    size_t n_cpus;

    using Matrix = Eigen::Matrix<scalar_type, Eigen::Dynamic, Eigen::Dynamic>;
    using Vector = Eigen::Matrix<scalar_type, Eigen::Dynamic, 1>;

    Matrix MatrixA() {
        return Matrix(n_cpus, n_cpus);
    }

    Matrix MatrixB() {
        return Matrix(n_cpus, n_cpus + 1);
    }

    Vector VectorA() {
        return Vector(n_cpus);
    }

    Vector VectorB() {
        return Vector(n_cpus + 1);
    }
};

template <size_t n_cpus_, class scalar_type_ = double>
class ThermalModel {
public:
    using scalar_type = scalar_type_;
    static constexpr size_t n_cpus = n_cpus_;

private:
    static_assert(
        std::numeric_limits<scalar_type>::has_infinity,
        "The type of scalar_type must be a type with an infinity value!");

private:
    template <size_t rows, size_t cols>
    using Matrix = Eigen::Matrix<scalar_type, rows, cols>;

    template <size_t rows>
    using Vector = Matrix<rows, 1>;

public:
    using MatrixA = Matrix<n_cpus, n_cpus>;
    using MatrixB = Matrix<n_cpus, n_cpus + 1>;
    using VectorA = Vector<n_cpus>;
    using VectorB = Vector<n_cpus + 1>;

private:
    // The two matrices that drive the model
    MatrixA A;
    MatrixB B;

    // Current input and initial condition
    scalar_type t0{0};
    VectorA T0 = VectorA::Zero();
    VectorB U = VectorB::Zero();

    // Other useful values to cache to optimize computation time
    MatrixA I = MatrixA::Identity();
    MatrixA Ainv;
    VectorA BU;

    // Builds the matrix A starting from the parameters
    MatrixA matrix_A(double C, double Re, const MatrixA R) {
        constexpr scalar_type inf =
            std::numeric_limits<scalar_type>::infinity();

        MatrixA Rcopy = R;
        Rcopy.diagonal().array() = inf;

        // Sum of the inverse of the values on each row
        VectorA Rsums = Rcopy.cwiseInverse().rowwise().sum();
        MatrixA A;

        for (size_t r = 0; r < n_cpus; ++r) {
            for (size_t c = 0; c < n_cpus; ++c) {
                if (r == c) {
                    A(r, c) = -1. / Re - Rsums(r);
                } else {
                    A(r, c) = 1 / R(r, c);
                }
            }
        }
        A /= C;
        return A;
    }

    // Builds the matrix B from the two parameters
    MatrixB matrix_B(double C, double Re) {
        MatrixB B;
        B.leftCols(n_cpus).setIdentity();
        B.rightCols(1) = VectorA::Constant(1 / Re);
        B /= C;
        return B;
    }

public:
    ThermalModel(scalar_type C, scalar_type Re, const scalar_type *R) :
        ThermalModel(C, Re, MatrixA(R)) {}

    ThermalModel(scalar_type C, scalar_type Re,
                 const std::vector<scalar_type> &R) :
        ThermalModel(C, Re, R.data()) {}

    ThermalModel(scalar_type C, scalar_type Re, const MatrixA &R) {
        A = matrix_A(C, Re, R);
        B = matrix_B(C, Re);
        Ainv = A.inverse();
        BU = B * U;
    }

    ThermalModel &initial(scalar_type t0_, const VectorA &T0_) {
        t0 = t0_;
        T0 = T0_;
        return *this;
    }

    ThermalModel &initial(scalar_type t0, const scalar_type *T0_) {
        // NOTE: this assumes that T0 is the correct size!
        return initial(t0, VectorA(T0_));
    }

    ThermalModel &initial(scalar_type t0, const std::vector<scalar_type> &T0_) {
        assert(T0_.size() == n_cpus);
        return initial(t0, T0_.data());
    }

    ThermalModel &initial(const VectorA &T0_) {
        return initial(0, T0_);
    }

    ThermalModel &initial(const scalar_type *T0_) {
        // NOTE: this assumes that T0 is the correct size!
        return initial(0, T0_);
    }

    ThermalModel &initial(const std::vector<scalar_type> &T0_) {
        return initial(0, T0_);
    }

    ThermalModel &input(const VectorA &P, scalar_type Te) {
        U.topRows(n_cpus) = P;
        U(n_cpus) = Te;

        // Cache new product
        BU = B * U;
        return *this;
    }

    ThermalModel &input(const scalar_type *P, scalar_type Te) {
        // NOTE: this assumes that P is the correct size!
        return input(VectorA(P), Te);
    }

    ThermalModel &input(const std::vector<scalar_type> &P, scalar_type Te) {
        assert(P.size() == n_cpus);
        return input(P.data(), Te);
    }

    // NOTE: t must be an absolute, non-negative, correctly scaled value in
    // seconds!
    ThermalModel &advance(scalar_type t) {
        VectorA T = compute_direct(t - t0);
        initial(t, T);
        return *this;
    }

    VectorA curtemp_Vector() {
        return T0;
    }

    std::vector<scalar_type> curtemp() {
        VectorA v = curtemp_Vector();
        return std::vector<scalar_type>(v.data(),
                                        v.data() + v.cols() * v.rows());
    }

private:
    VectorA compute_direct(scalar_type t) {
        VectorA free, forced, res;
        MatrixA Atexp = (A * t).exp();

        // Free response:
        // free(t) = exp(A * t) * T0
        free = Atexp * T0;

        // Forced response:
        // forced(t) = - A^(-1) * (I - exp(A * t)) * B * U
        forced = -Ainv * (I - Atexp) * BU;

        // Output is the sum of the two
        res = free + forced;

        return res;
    }
};
