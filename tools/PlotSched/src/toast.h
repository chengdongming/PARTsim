#ifndef TOAST_H
#define TOAST_H

#include <QMessageBox>


class Toast : public QMessageBox
{
public:
    Toast(const QString &message);
    void show();
    static void show(const QString &message);
private:
    void timerTriggered();
};

#endif // TOAST_H
