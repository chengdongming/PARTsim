#include "tracefilelister.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLabel>
#include <QTreeWidgetItem>
#include <QVBoxLayout>

#include <QDebug>

TraceFileLister::TraceFileLister(QWidget *parent) :
    QDockWidget("Traces", parent) {
    tree = new QTreeWidget(this);
    tree->setColumnCount(1);

    tree->clear();
    tree->setHeaderLabel("Filename");

    this->setWidget(tree);

    connect(tree, SIGNAL(itemClicked(QTreeWidgetItem *, int)), this,
            SLOT(traceSelected(QTreeWidgetItem *, int)));
}

TraceFileLister::~TraceFileLister() {}

void TraceFileLister::addFolderToTree(QTreeWidgetItem *parent, QString dir) {
    QDir *root = new QDir(dir);

    QFileInfoList l = root->entryInfoList(QDir::NoDotAndDotDot | QDir::Dirs);

    QStringList filters;
    filters << "*.pst";
    root->setNameFilters(filters);

    l.append(root->entryInfoList(QDir::Files));

    QTreeWidgetItem *j;
    for (QFileInfo o : l) {
        j = new QTreeWidgetItem(parent);
        j->setText(0, o.fileName());

        QString newDir = root->absoluteFilePath(o.fileName());
        QFileInfo *fi = new QFileInfo(newDir);
        if (fi->isDir()) {
            addFolderToTree(j, newDir);
        }
        delete fi;
    }

    delete root;
}

void TraceFileLister::update(QString path) {
    QFileInfo *fi = new QFileInfo(path);

    if (fi->isDir()) {
        tree->clear();
        tree->setHeaderLabel(fi->absoluteFilePath());

        QTreeWidgetItem *j = new QTreeWidgetItem(tree);
        j->setText(0, fi->absoluteFilePath());
        addFolderToTree(j, path);
        j->setExpanded(true);
    }

    delete fi;
}

void TraceFileLister::traceSelected(QTreeWidgetItem *i, int col) {
    QString fullPath;

    do {
        fullPath.insert(0, "/" + i->text(col));
        i = i->parent();
    } while (i != 0);

    fullPath.remove(0, 1);

    emit traceChosen(fullPath);
}
