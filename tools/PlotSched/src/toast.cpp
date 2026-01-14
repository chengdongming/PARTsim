#include "toast.h"

#include <QApplication>
#include <QCoreApplication>
#include <QDebug>
#include <QDesktopWidget>
#include <QGuiApplication>
#include <QScreen>
#include <QString>
#include <QThread>
#include <QTimer>

Toast::Toast(const QString &message) {
    setText(message);
    setStandardButtons(0); // no buttons

    int x = QGuiApplication::primaryScreen()->geometry().width() / 2 - 80;
    int y = QGuiApplication::primaryScreen()->geometry().height() - 120;
    move(x, y);
}

void Toast::show() {
    QTimer *timer = new QTimer();
    QObject::connect(timer, SIGNAL(timeout()), this, SLOT(hide()));
    timer->start(500); // ms

    //    QMessageBox::show();
    exec(); // blocking :-(
}

void Toast::show(const QString &message) {
    Toast(message).show();
}
