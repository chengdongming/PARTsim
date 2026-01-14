#ifndef PLOTFRAME_H
#define PLOTFRAME_H

#include <QGraphicsItemGroup>
#include <QList>
#include <QVector>
#include <QString>
#include <QGraphicsSimpleTextItem>

class PlotFrame : public QGraphicsItemGroup
{
  qreal vertical_offset;

  QList<QGraphicsSimpleTextItem *> callers; // the tasks
  QVector<QGraphicsLineItem *> lines;

public:
  PlotFrame(qreal offset = 50, QGraphicsItem * parent = 0);

  /// add a row to the plot: t0 |____________
  void addRow(const QString &title);
  void setWidth(qreal width);
  /// add a column to the plot (see cpp)
  void addColumn(const QString &title);
};

#endif // PLOTFRAME_H
