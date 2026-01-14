#include "event.h"

#include <QMap>
#include <QTextStream>

#include <QDebug>

QMap<QString, QMap<event_kind, Event *>> pending_events;

void Event::parseLine(QByteArray line) {
    // TODO
    QString status;

    correct = false;

    QTextStream ss(line);

    ss >> time_start;
    ss >> caller;
    ss >> cpu;
    ss >> event;
    ss >> status;

    if (event == "RUNNING") {
        kind = RUNNING;
    } else if (event == "DEAD") {
        kind = DEAD;
    } else if (event == "BLOCKED") {
        kind = BLOCKED;
    } else if (event == "ACTIVATION" || event == "CREATION") {
        kind = ACTIVATION;
    } else if (event == "CONFIGURATION") {
        kind = CONFIGURATION;
    } else if (event == "DEADLINE") {
        kind = DEADLINE;
    } else if (event == "MISS") {
        kind = MISS;
    }

    if (status == "I") {
        correct = true;
        pending = false;
    } else if (status == "E") {
        pending = false;
        if (pending_events[caller].find(kind) != pending_events[caller].end()) {
            duration = time_start -
                       pending_events[caller].find(kind).value()->getStart();
            this->time_start =
                pending_events[caller].find(kind).value()->getStart();
            correct = true;

            Event *ev = pending_events[caller].find(kind).value();
            pending_events[caller].remove(kind);
            delete ev;
        }
    } else if (status == "S") {
        pending = true;
        Event *ev = new Event(*this);
        pending_events[caller].insert(kind, ev);
    }
    /*
    qDebug() << "Read from device : " << time_start;
    qDebug() << "Read from device : " << caller;
    qDebug() << "Read from device : " << cpu;
    qDebug() << "Read from device : " << event;
    qDebug() << "Read from device : " << status;

    qDebug() << "";
    */
}

bool correctLine(QByteArray line) {
    // TODO

    if (line.size() < 2)
        return false;
    return true;
}

Event::Event() {
    magnification = 1;
    duration = 0;
    correct = false;
}

void Event::parse(QByteArray line) {
    if (correctLine(line))
        parseLine(line);
}

Event::Event(const Event &o) : QObject() {
    time_start = o.time_start;
    duration = o.duration;
    cpu = o.cpu;
    row = o.row;
    caller = o.caller;
    event = o.event;
    kind = o.kind;

    magnification = o.magnification;

    correct = o.correct;
    pending = o.pending;
    range = o.range;
}

Event &Event::operator=(const Event &o) {
    time_start = o.time_start;
    duration = o.duration;
    cpu = o.cpu;
    row = o.row;
    caller = o.caller;
    event = o.event;
    kind = o.kind;

    magnification = o.magnification;

    correct = o.correct;
    pending = o.pending;
    range = o.range;

    return *this;
}

bool Event::isCorrect() {
    return correct;
}

bool Event::isPending() {
    return pending;
}

bool Event::isRange() {
    return range;
}

unsigned long Event::getStart() {
    return time_start;
}

unsigned long Event::getDuration() {
    return duration;
}

QString Event::getCaller() {
    return caller;
}

QString Event::getCPU() {
    return cpu;
}

event_kind Event::getKind() {
    return kind;
}
