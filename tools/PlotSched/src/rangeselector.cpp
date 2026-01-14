#include "rangeselector.h"

#include <QPen>

RangeSelector::RangeSelector() {
    horizontal = new QGraphicsLineItem(this);
    vertical_left = new QGraphicsLineItem(this);
    vertical_right = new QGraphicsLineItem(this);
    sym_h = new QGraphicsLineItem(this);
    sym_v = new QGraphicsLineItem(this);

    QPen p;

    p.setWidth(2);

    horizontal->setPen(p);
    vertical_left->setPen(p);
    vertical_right->setPen(p);
    sym_h->setPen(p);
    sym_v->setPen(p);

    this->addToGroup(horizontal);
    this->addToGroup(vertical_left);
    this->addToGroup(vertical_right);
    this->addToGroup(sym_h);
    this->addToGroup(sym_v);

    setVisible(false);
}

void RangeSelector::update() {
    horizontal->setLine(start.x(), start.y(), end.x(), start.y());
    vertical_left->setLine(start.x(), start.y() - 5, start.x(), start.y() + 5);
    vertical_right->setLine(end.x(), start.y() - 5, end.x(), start.y() + 5);

    qreal sym_x = end.x() - 8;
    qreal sym_y = start.y() - 8;

    sym_h->setLine(sym_x - 5, sym_y, sym_x + 5, sym_y);
    sym_v->setLine(sym_x, sym_y - 5, sym_x, sym_y + 5);

    sym_v->setVisible(start.x() < end.x());
}

void RangeSelector::setStartPoint(QPointF p) {
    start = p;
    update();
}

void RangeSelector::setEndPoint(QPointF p) {
    end = p;
    update();
}

void RangeSelector::setVisible(bool v) {
    update();

    horizontal->setVisible(v);
    vertical_left->setVisible(v);
    vertical_right->setVisible(v);

    sym_h->setVisible(v);
    sym_v->setVisible(v);
}
