#include "plotframe.h"

PlotFrame::PlotFrame(qreal offset, QGraphicsItem *parent) :
    QGraphicsItemGroup(parent) {
    vertical_offset = offset;
}

/// adds a row to the plot
/// t0 |___________
void PlotFrame::addRow(const QString &title) {
    unsigned int count = callers.count();

    qreal y = count * vertical_offset;

    QGraphicsSimpleTextItem *t = new QGraphicsSimpleTextItem(title, this);
    t->setPos(-t->boundingRect().width() - 10, y - t->boundingRect().height());
    callers.append(t);
    this->addToGroup(t);

    // the horizontal line (indicating advancing time) of: t0 |______________
    QGraphicsLineItem *l = new QGraphicsLineItem(0, y, 0, y, this);
    lines.append(l);
    this->addToGroup(l);
}

/// adds a column to the plot. Use either addColumn() or addRow().
/// |      |
/// |      |
///  ______
/// | BIG0 |
///  ------
void PlotFrame::addColumn(const QString &title) {
    unsigned int count = callers.count();

    qreal y = count * vertical_offset;

    QGraphicsSimpleTextItem *t = new QGraphicsSimpleTextItem(title, this);
    t->setPos(-t->boundingRect().width() - 10, y - t->boundingRect().height());
    callers.append(t);
    this->addToGroup(t);

    // left vertical line
    QGraphicsLineItem *lleft = new QGraphicsLineItem(
        t->boundingRect().x(), 0, t->boundingRect().x(), -300, this);
    lines.append(lleft);
    this->addToGroup(lleft);

    QGraphicsLineItem *lright = new QGraphicsLineItem(
        t->boundingRect().width(), 0, t->boundingRect().width(), -300, this);
    lines.append(lright);
    this->addToGroup(lright);
}

void PlotFrame::setWidth(qreal width) {
    for (QVector<QGraphicsLineItem *>::iterator it = lines.begin();
         it != lines.end(); ++it) {
        QLineF old_line = (*it)->line();
        (*it)->setLine(0, old_line.y1(), width, old_line.y1());
    }
}
