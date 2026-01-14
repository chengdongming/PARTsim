#include "eventview.h"

#include <QBrush>
#include <QGraphicsLineItem>
#include <QGraphicsSimpleTextItem>
#include <QPen>

QColor EventView::eventToColor(event_kind e) {
    switch (e) {
    case RUNNING:
        return Qt::green;
    case BLOCKED:
        return Qt::red;
    case CONFIGURATION:
        return Qt::yellow;
    default:
        return Qt::white;
    }
}

EventView::EventView(const Event &e, qreal offset, QGraphicsItem *parent) :
    QGraphicsItemGroup(parent) {
    height = 30;
    vertical_offset = offset;

    setEvent(e);
}

// EventView::EventView(const Event &e, qreal offset, QGraphicsItem * parent) :
//   QGraphicsItemGroup(parent)
//{
//   height = 30;
//   vertical_offset = offset;

//  setEvent(e);
//}

EventView::~EventView() {
    delete e_;
}

/// Represent the event with a rectangle
void EventView::setEvent(Event e) {
    // drawCircle(50, 50, 4);
    // drawCircle(0, 0, 4);

    qDeleteAll(this->childItems());

    e_ = new Event(e);

    drawText();
    QGraphicsRectItem *rect;

    switch (e.getKind()) {
    case RUNNING:
        rect = drawRect(e.getDuration() * e.getMagnification(),
                        eventToColor(e.getKind()));
        drawTextInRect(rect, e_->getCaller());
        break;
    case BLOCKED:
        rect = drawRect(e.getDuration() * e.getMagnification(),
                        eventToColor(e.getKind()));
        drawTextInRect(rect, e_->getCaller());
        break;
    case ACTIVATION:
        drawArrowUp();
        break;
    case DEADLINE:
        drawArrowDown();
        break;
    case CONFIGURATION:
        drawRectH(e.getDuration() * e.getMagnification(),
                  eventToColor(e.getKind()));
        break;
    case MISS:
        drawArrowDownRed();
        break;
    case DEAD:
        drawCircle();
        break;
    default:
        return;
    }

    this->moveBy(e.getStart() * e.getMagnification(),
                 vertical_offset * e.getRow()); // TODO
}

void EventView::drawCircle() {
    QGraphicsEllipseItem *body =
        new QGraphicsEllipseItem(-4, -4, 4 * 2, 4 * 2, this);
    this->addToGroup(body);
}

void EventView::drawArrowUp() {
    /******************************
     *
     *                / (x2, y2)
     *              ///
     *            / / /
     *   (left) /  /  / (right)
     *            /
     *           /
     *          /
     *         / (x1, y1)
     *
     ******************************/

    // First of all, create an arrow with (x1, y1) = (0, 0)

    QGraphicsLineItem *body = new QGraphicsLineItem(0, 0, 0, height, this);
    QGraphicsLineItem *left =
        new QGraphicsLineItem(0, 0, height / 5.0, height / 4.0, this);
    QGraphicsLineItem *right =
        new QGraphicsLineItem(0, 0, -height / 5.0, height / 4.0, this);

    body->moveBy(0, -height);
    left->moveBy(0, -height);
    right->moveBy(0, -height);

    this->addToGroup(body);
    this->addToGroup(left);
    this->addToGroup(right);
}

void EventView::drawArrowDown() {
    QGraphicsLineItem *body = new QGraphicsLineItem(0, 0, 0, height, this);
    QGraphicsLineItem *left = new QGraphicsLineItem(0, height, height / 5.0,
                                                    height * 3.0 / 4.0, this);
    QGraphicsLineItem *right = new QGraphicsLineItem(0, height, -height / 5.0,
                                                     height * 3.0 / 4.0, this);

    body->moveBy(0, -height);
    left->moveBy(0, -height);
    right->moveBy(0, -height);

    this->addToGroup(body);
    this->addToGroup(left);
    this->addToGroup(right);
}

void EventView::drawArrowDownRed() {
    QGraphicsLineItem *body = new QGraphicsLineItem(0, 0, 0, height, this);
    QGraphicsLineItem *left = new QGraphicsLineItem(0, height, height / 5.0,
                                                    height * 3.0 / 4.0, this);
    QGraphicsLineItem *right = new QGraphicsLineItem(0, height, -height / 5.0,
                                                     height * 3.0 / 4.0, this);

    QPen p;
    p.setColor(Qt::red);
    p.setWidth(2);

    body->setPen(p);
    left->setPen(p);
    right->setPen(p);

    body->moveBy(0, -height);
    left->moveBy(0, -height);
    right->moveBy(0, -height);

    this->addToGroup(body);
    this->addToGroup(left);
    this->addToGroup(right);
}

QGraphicsRectItem *EventView::drawRect(qreal duration, QColor color) {
    /******************************
     *
     *          ____________
     *         |            |
     *         |            | height
     *         |____________|
     *  (x1, y1)  duration
     *
     ******************************/

    qreal rectHeight = height / 1.9;

    QGraphicsRectItem *r =
        new QGraphicsRectItem(0, 0, duration, rectHeight, this);
    r->setBrush(QBrush(color));

    r->moveBy(0, -rectHeight);

    this->addToGroup(r);
    return r;
}

void EventView::drawRectH(qreal duration, QColor color) {
    /******************************
     *
     *          ____________
     *         |            |
     *         |            | height
     *         |____________|
     *  (x1, y1)  duration
     *
     ******************************/

    qreal rectHeight = height / 1.5;

    QGraphicsRectItem *r =
        new QGraphicsRectItem(0, 0, duration, rectHeight, this);
    r->setBrush(QBrush(color));

    r->moveBy(0, -rectHeight);

    this->addToGroup(r);
}

void EventView::drawText() {
    QGraphicsSimpleTextItem *start =
        new QGraphicsSimpleTextItem(QString::number(e_->getStart()), this);
    start->setPos(0, 0);
    this->addToGroup(start);

    if (e_->getDuration() > 0) {
        QGraphicsSimpleTextItem *end = new QGraphicsSimpleTextItem(
            QString::number(e_->getStart() + e_->getDuration()), this);
        end->setPos(0, 0);
        end->moveBy(e_->getDuration() * e_->getMagnification(), 0);
        this->addToGroup(end);
    }
}

// adds text inside rect
void EventView::drawTextInRect(QGraphicsRectItem *rect, const QString &text) {
    QGraphicsSimpleTextItem *start = new QGraphicsSimpleTextItem(text, this);
    start->setPos(rect->pos().x() + 3, rect->pos().y());
    this->addToGroup(start);
}
