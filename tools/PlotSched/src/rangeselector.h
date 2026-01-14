#ifndef RANGESELECTOR_H
#define RANGESELECTOR_H

#include <QGraphicsLineItem>
#include <QGraphicsItemGroup>

class RangeSelector : public QGraphicsItemGroup
{
  QGraphicsLineItem * horizontal;
  QGraphicsLineItem * vertical_left;
  QGraphicsLineItem * vertical_right;

  QGraphicsLineItem * sym_h; // For creating the symbol '+' or '-'
  QGraphicsLineItem * sym_v;

  QPointF start;
  QPointF end;

  void update();

public:
  RangeSelector();
  void setStartPoint(QPointF p);
  void setEndPoint(QPointF p);
  void setVisible(bool v = true);
};

#endif // RANGESELECTOR_H
