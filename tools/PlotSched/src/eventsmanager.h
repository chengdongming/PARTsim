#ifndef EVENTSMANAGER_H
#define EVENTSMANAGER_H

#include <QObject>
#include <QMap>
#include <QList>
#include <QString>

#include "event.h"

class EventsManager : public QObject
{
  Q_OBJECT

  QMap<QString, QList<Event>> events_container;
  qreal last_event;
  qreal last_magnification;

public:
    EventsManager();
    void clear();
    unsigned long countCallers();
    QList<Event> * getCallerEventsList(unsigned long caller);
    QMap <QString, QList<Event>> * getCallers();
    QMap <QString, QList<Event>> * getCPUs();

    QList<QString> getCPUList();
    QMap<QString, QList<QString> > *getTasks(QString core, unsigned int time);
signals:

public slots:
    void newEventArrived(Event e);
    qreal magnify(qreal start, qreal end, qreal width);
};

#endif // EVENTSMANAGER_H
