#include "event.h"
#include "mainwindow.h"
#include <QApplication>

#include <QDebug>

int main(int argc, char *argv[]) {
    QApplication a(argc, argv);
    MainWindow *w;

    qRegisterMetaType<Event>("Event");

    if (argc == 2) {
        w = new MainWindow(argv[1]);
    } else {
        w = new MainWindow();
    }
    w->show();

    return a.exec();
}
