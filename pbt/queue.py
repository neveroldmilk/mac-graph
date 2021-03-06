
import traceback
import pickle
import time

import logging
logger = logging.getLogger(__name__)

import pika

class Queue(object):

	def __init__(self, args, logger):
		self.args = args
		self.logger = logger

	def send(self, message):
		pass

	def get_messages(self, callback):
		"""
			callback: (spec, ack, nack) -> None
			It's expected that callback will ack/nack the message
		"""
		pass

	def _handle_message(self, data, callback, ack, nack):
		try:
			spec = pickle.loads(data)
		except Exception:
			logger.debug("Could not unpickle message")
			ack()
			return

		if time.time() - spec.time_sent < self.args.message_timeout:
			if spec.group != self.args.run:
				self.logger.debug("Message for other group {}".format(spec.group))
				nack()
			else:
				self.logger.debug("Received {}".format(spec))
				callback(spec, ack, nack)
		else:
			self.logger.debug("Timed out message")
			ack()

	def close(self, callback):
		pass

class QueueFactory(object):

	@classmethod
	def vend(clz, args, *argv):
		if args.queue_type == "rabbitmq":
			return RabbitQueue(args, *argv)
		else:
			raise ValueError(args.queue_type)


class RabbitQuickChannel(object):

	default_expiry = 1000 * 60 * 60 * 5

	def __init__(self, args, exchange, queue, topic):
		self.args = args
		self.topic = topic
		self.queue = queue
		self.exchange = exchange

		parameters = pika.URLParameters(args.amqp_url)
		self.connection = pika.BlockingConnection(parameters)

		self.channel = self._open_channel()

	def _open_channel(self):
		channel = self.connection.channel()
		channel.basic_qos(prefetch_count=1)

		channel.exchange_declare(exchange=self.exchange, exchange_type='topic', arguments={
			'x-expires': RabbitQuickChannel.default_expiry
		})
		channel.queue_declare(
			queue=self.queue, 
			durable=True,
			arguments={
				'x-message-ttl' : 1000*self.args.message_timeout,
				'x-expires': RabbitQuickChannel.default_expiry
			})
		channel.queue_bind(queue=self.queue, exchange=self.exchange, routing_key=self.topic)

		return channel

	def __enter__(self):
		return self.channel

	def __exit__(self ,type, value, traceback):
		self.channel.close()
		self.connection.close()


class RabbitQueue(Queue):

	connection = None

	def __init__(self, args, exchange, queue, topic):
		logger = logging.getLogger(__name__ + "." + exchange + "." + queue + "." + topic)
		super().__init__(args, logger)

		self.topic    = args.run + "." + topic
		self.queue    = args.run + "." + queue
		self.exchange = args.run + "." + exchange


	def send(self, message):
		body = pickle.dumps(message)

		with RabbitQuickChannel(self.args, self.exchange, self.queue, self.topic) as channel:
			channel.basic_publish(
				exchange=self.exchange,
				routing_key=self.topic,
				body=body,
				properties=pika.BasicProperties(
					delivery_mode = 2, # make message persistent
				))
			self.logger.debug("Sent {}".format(message))

	def get_messages(self, callback, limit=None):
		
		def _callback(ch, method, properties, body):
			def ack():
				ch.basic_ack(delivery_tag = method.delivery_tag)
				self.logger.debug("ACK {}".format(body))

			def nack():
				ch.basic_nack(delivery_tag = method.delivery_tag)
				self.logger.debug("NACK {}".format(body))
			
			# self.logger.debug("Received")

			self._handle_message(body, callback, ack, nack)

		messages = []

		with RabbitQuickChannel(self.args, self.exchange, self.queue, self.topic) as channel:
			i = 0
			while limit is None or i < limit:
				method, properties, body = channel.basic_get(queue=self.queue, no_ack=True)
				if body is not None:
					messages.append(body)
					i += 1
				else: 
					break
				
		# self.logger.debug("Received {} messages".format(len(messages)))

		for i in messages:
			self._handle_message(i, callback, lambda:True, lambda:True)

	def close(self):

		pass

