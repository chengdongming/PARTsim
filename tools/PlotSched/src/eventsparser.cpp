#include "eventsparser.h"

#include <QDebug>

EventsParser::EventsParser() {
    EventsParserWorker *worker = new EventsParserWorker;

    worker->moveToThread(&workerThread);
    connect(&workerThread, &QThread::finished, worker, &QObject::deleteLater);
    connect(this, &EventsParser::operate, worker, &EventsParserWorker::doWork);
    connect(worker, SIGNAL(fileParsed()), this, SLOT(handleResults()));
    connect(worker, SIGNAL(eventGeneratedByWorker(Event)), this,
            SLOT(eventGeneratedByWorker(Event)));

    workerThread.start();
}

void EventsParser::parseFile(QString path) {
    emit operate(path);
}

EventsParser::~EventsParser() {
    workerThread.quit();
    workerThread.wait();
}

void EventsParser::handleResults() {
    emit fileParsed();
}

void EventsParser::eventGeneratedByWorker(Event e) {
    emit eventGenerated(e);
}

void EventsParserWorker::doWork(QString path) {
    QString result;
    QFile f(path);

    if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        while (!f.atEnd()) {
            Event e;
            e.parse(f.readLine());
            if (e.isCorrect()) {
                emit eventGeneratedByWorker(e);
            }
        }
    }

    emit fileParsed();
}
