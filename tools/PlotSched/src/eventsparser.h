#ifndef EVENTSPARSER_H
#define EVENTSPARSER_H

#include "event.h"

#include <QObject>
#include <QFile>
#include <QThread>
#include <QGraphicsItem>

#include <QDebug>

class EventsParserWorker : public QObject
{
  Q_OBJECT

public slots:
  void doWork(QString path);

signals:
  void fileParsed();
  void eventGeneratedByWorker(Event);
};

class EventsParser : public QObject
{
  Q_OBJECT
  QThread workerThread;

public:
  explicit EventsParser();
  ~EventsParser();
  void parseFile(QString);

public slots:
  void handleResults();
  void eventGeneratedByWorker(Event);

signals:
  void operate(QString);
  void eventGenerated(Event);
  void fileParsed();
};

#endif // EVENTSPARSER_H
