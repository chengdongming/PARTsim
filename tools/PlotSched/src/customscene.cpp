#include "customscene.h"

#include <QGraphicsSceneMouseEvent>

#include <QDebug>

CustomScene::CustomScene(QObject *parent) : QGraphicsScene(parent) {
    pressed = false;
    range = 0;
}

void CustomScene::mousePressEvent(QGraphicsSceneMouseEvent *mouseEvent) {
    // qDebug() << "Mouse pressed at " << mouseEvent->lastScenePos();

    switch (mouseEvent->button()) {
    case Qt::LeftButton:
        qDebug() << "Mouse pressed at : " << mouseEvent->lastScenePos();
        qDebug() << "Scene width : " << this->width();
        break;
    case Qt::RightButton:
        pressed = true;
        pressed_at = mouseEvent->lastScenePos();

        range = new RangeSelector;
        range->setStartPoint(pressed_at);
        range->setEndPoint(pressed_at);
        this->addItem(range);
        range->setVisible();
        break;
    default:
        break;
    }
}

void CustomScene::mouseReleaseEvent(QGraphicsSceneMouseEvent *mouseEvent) {
    // qDebug() << "Mouse released at " << mouseEvent->lastScenePos();

    switch (mouseEvent->button()) {
    case Qt::LeftButton:
        break;
    case Qt::RightButton:
        this->removeItem(range);
        delete range;
        range = 0;
        pressed = false;

        if (pressed_at.x() != mouseEvent->lastScenePos().x())
            emit rangeSelected(pressed_at.x(), mouseEvent->lastScenePos().x());
        break;
    default:
        break;
    }
}

void CustomScene::mouseMoveEvent(QGraphicsSceneMouseEvent *mouseEvent) {
    if (!pressed)
        return;

    range->setEndPoint(mouseEvent->lastScenePos());
    // qDebug() << "Moving mouse to " << mouseEvent->lastScenePos();
}
