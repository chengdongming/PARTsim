#ifndef CUSTOMSCENE_H
#define CUSTOMSCENE_H

#include <QGraphicsScene>

#include "rangeselector.h"

class CustomScene : public QGraphicsScene
{
  Q_OBJECT

  RangeSelector * range;

  bool pressed;
  QPointF pressed_at;

public:
  explicit CustomScene(QObject *parent = 0);

signals:
  void rangeSelected(qreal, qreal);

public slots:
  virtual void mousePressEvent(QGraphicsSceneMouseEvent * mouseEvent);
  virtual void mouseMoveEvent(QGraphicsSceneMouseEvent *mouseEvent);
  virtual void mouseReleaseEvent(QGraphicsSceneMouseEvent * mouseEvent);

};

#endif // CUSTOMSCENE_H
