#include "customtoolbar.h"

#include <QToolButton>

CustomToolBar::CustomToolBar(QWidget *parent) : QToolBar(parent) {
    QToolButton *buttonOpen = new QToolButton(this);
    buttonOpen->setIcon(QIcon::fromTheme("document-open"));
    this->addWidget(buttonOpen);

    connect(buttonOpen, SIGNAL(clicked()), this, SLOT(buttonOpenSlot()));

    QToolButton *buttonRefresh = new QToolButton(this);
    buttonRefresh->setIcon(QIcon::fromTheme("view-refresh"));
    this->addWidget(buttonRefresh);

    connect(buttonRefresh, SIGNAL(clicked()), this, SLOT(buttonRefreshSlot()));

    this->addSeparator();

    QToolButton *buttonZoomIn = new QToolButton(this);
    buttonZoomIn->setIcon(QIcon::fromTheme("zoom-in"));
    this->addWidget(buttonZoomIn);

    connect(buttonZoomIn, SIGNAL(clicked()), this, SLOT(buttonZoomInSlot()));

    QToolButton *buttonZoomOut = new QToolButton(this);
    buttonZoomOut->setIcon(QIcon::fromTheme("zoom-out"));
    this->addWidget(buttonZoomOut);

    connect(buttonZoomOut, SIGNAL(clicked()), this, SLOT(buttonZoomOutSlot()));

    QToolButton *buttonZoomFit = new QToolButton(this);
    buttonZoomFit->setIcon(QIcon::fromTheme("zoom-fit-best"));
    this->addWidget(buttonZoomFit);

    connect(buttonZoomFit, SIGNAL(clicked()), this, SLOT(buttonZoomFitSlot()));

    QToolButton *buttonChangeTasksView = new QToolButton(this);
    buttonChangeTasksView->setIcon(QIcon(":/icons/assets/tau64x.png"));
    buttonChangeTasksView->setToolTip("Change scheduling view: tasks");
    this->addWidget(buttonChangeTasksView);
    connect(buttonChangeTasksView, SIGNAL(clicked()), this,
            SLOT(buttonChangeViewTasksSlot()));

    QToolButton *buttonChangeGanntView = new QToolButton(this);
    buttonChangeGanntView->setIcon(QIcon(":/icons/assets/gannt64x.png"));
    buttonChangeGanntView->setToolTip("Change scheduling view: Gannt diagram");
    this->addWidget(buttonChangeGanntView);
    connect(buttonChangeGanntView, SIGNAL(clicked()), this,
            SLOT(buttonChangeViewGanntSlot()));

    QToolButton *buttonChangeCPUView = new QToolButton(this);
    buttonChangeCPUView->setIcon(QIcon(":/icons/assets/cpu64x.png"));
    buttonChangeCPUView->setToolTip("Change scheduling view: cores load");
    this->addWidget(buttonChangeCPUView);
    connect(buttonChangeCPUView, SIGNAL(clicked()), this,
            SLOT(buttonChangeViewCPUSlot()));

    this->addSeparator();
}

void CustomToolBar::buttonOpenSlot() {
    emit openButtonClicked();
}

void CustomToolBar::buttonRefreshSlot() {
    emit refreshButtonClicked();
}

void CustomToolBar::buttonZoomInSlot() {
    emit zoomInClicked();
}

void CustomToolBar::buttonZoomOutSlot() {
    emit zoomOutClicked();
}

void CustomToolBar::buttonZoomFitSlot() {
    emit zoomFitClicked();
}

void CustomToolBar::buttonChangeViewGanntSlot() {
    emit changeViewGanntClicked();
}

void CustomToolBar::buttonChangeViewCPUSlot() {
    emit changeViewCPUClicked();
}

void CustomToolBar::buttonChangeViewTasksSlot() {
    emit changeViewTasksClicked();
}
