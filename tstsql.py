import pymysql

conn=pymysql.connect(host='localhost',port=3306,user='root',password='123456',database='test')
cursor=conn.cursor()
cursor.execute("show tables")
print(cursor.fetchall())
cursor.close()
conn.close()
