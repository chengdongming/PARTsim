#ifndef TRACEFILELISTER_H
#define TRACEFILELISTER_H

#include <QDockWidget>
#include <QTreeWidget>

class TraceFileLister : public QDockWidget
{
  Q_OBJECT

  QTreeWidget * tree;

  void addFolderToTree(QTreeWidgetItem * parent, QString dir);

private slots:
  void traceSelected(QTreeWidgetItem * i, int col);

public:
  explicit TraceFileLister(QWidget *parent = 0);
  ~TraceFileLister();

public slots:
  void update(QString);

signals:
  void traceChosen(QString);
};

#endif // TRACEFILELISTER_H
