#ifndef PLOT_H
#define PLOT_H

#include <QWidget>

#include <QGraphicsView>
#include <QGraphicsItem>

#include "customscene.h"

class Plot : public QWidget
{
  Q_OBJECT

  CustomScene * scene;
  QGraphicsView * view;

public:
  explicit Plot(QWidget *parent = 0);
  qreal updateSceneView(qreal center);
  void clear();

signals:
  void zoomChanged(qreal, qreal, qreal);

public slots:
  void addNewItem(QGraphicsItem * i);
  void rangeSelected(qreal, qreal);
};

#endif // PLOT_H
