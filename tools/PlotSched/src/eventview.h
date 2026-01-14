#ifndef EVENTVIEW_H
#define EVENTVIEW_H

#include <QGraphicsItem>
#include <QGraphicsItemGroup>
#include "event.h"

class EventView : public QGraphicsItemGroup
{
  qreal height;
  qreal vertical_offset;
  Event * e_;

  void setEvent(Event e);
  QColor eventToColor(event_kind e);
  void drawText();

public:
  explicit EventView(const Event &e, qreal offset = 50, QGraphicsItem * parent = 0);
  ~EventView();

  void drawArrowUp();
  void drawArrowDown();
  void drawArrowDownRed();
  QGraphicsRectItem *drawRect(qreal duration, QColor color);
  void drawRectH(qreal duration, QColor color);
  void drawCircle();
protected:
  void drawTextInRect(QGraphicsRectItem *rect, const QString &text);
};

#endif // EVENTVIEW_H
