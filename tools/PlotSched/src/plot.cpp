#include "plot.h"

#include <QGridLayout>
#include <QWheelEvent>

#include <QDebug>

Plot::Plot(QWidget *parent) : QWidget(parent) {
    scene = new CustomScene(this);
    view = new QGraphicsView(scene);

    this->setLayout(new QGridLayout(this));
    this->layout()->addWidget(view);

    connect(scene, SIGNAL(rangeSelected(qreal, qreal)), this,
            SLOT(rangeSelected(qreal, qreal)));
}

void Plot::addNewItem(QGraphicsItem *i) {
    scene->addItem(i);
}

void Plot::clear() {
    scene->clear();
}

void Plot::rangeSelected(qreal init, qreal end) {
    qDebug() << "Zooming range selected : " << init << " " << end;
    qDebug() << "Center point : " << (init + end) / 2;
    qDebug() << "View width : " << view->width();
    emit zoomChanged(init, end, view->width());
}

qreal Plot::updateSceneView(qreal center) {
    scene->setSceneRect(scene->itemsBoundingRect());

    // qreal old_x = scene->itemsBoundingRect().x();
    qreal old_y = scene->itemsBoundingRect().y();
    // qreal old_w = scene->itemsBoundingRect().width();
    // qreal old_h = scene->itemsBoundingRect().height();

    // qDebug() << "The new esureVisible is : " << start << " " << end;
    qDebug() << "The center is : " << center;

    view->centerOn(center, old_y);
    /*

    qreal size = end - start;
    if (size > 0) {
      view->ensureVisible(start, old_y, end, old_h);
      //view->centerOn((init + end) / 2, view->pos().y());
    }
    */

    return scene->itemsBoundingRect().right();
}
